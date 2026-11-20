import asyncio
import json
import re
import statistics
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from agent.agent import Agent
from core.settings import settings
from core.tracing import flush_traces, tracer_callbacks
from services.vector_service import VectorStoreService

HERE = Path(__file__).resolve().parent
JUDGE_MODEL = (
    settings.VALIDATION_MODEL
)  # stronger than the agent's gpt-4o-mini: a model should not grade itself
THRESHOLDS = {"rules_pass_rate": 0.90, "faithfulness": 4.0, "relevance": 4.0}
# one failure in these types fails the run: averages hide a single hallucination or a missed refusal
CRITICAL_TYPES = {"out_of_scope", "no_answer", "partial"}
SOURCE_RE = re.compile(r"\[source: ([^\]]+)\]")
CODE_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.S)
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
CYR_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
NO_INFO = (
    "don't have",
    "do not have",
    "not cover",
    "no information",
    "doesn't cover",
    "not mention",
)


def _squash(s: str) -> str:
    """Whitespace-insensitive form for comparing code."""
    return " ".join(s.split())


class Verdict(BaseModel):
    reasoning: str = Field(description="Short analysis written BEFORE the scores.")
    faithfulness: int = Field(
        ge=1,
        le=5,
        description="5 = every claim is supported by the CONTEXT, 1 = mostly invented.",
    )
    relevance: int = Field(
        ge=1, le=5, description="5 = the answer fully addresses the question."
    )
    correctness: int = Field(
        ge=1,
        le=5,
        description="5 = agrees with the REFERENCE facts; 3 if there are none.",
    )
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Claims not found in the CONTEXT."
    )


JUDGE_PROMPT = """You are a strict evaluator of a RAG assistant that answers questions about Andriy from his notes.
Judge ONLY against the CONTEXT (the tool results the assistant saw). A claim that is not in the CONTEXT
is unsupported, even if it is true in general. Code must match the CONTEXT exactly.

QUESTION: {question}
REFERENCE FACTS (may be empty): {reference}
CONTEXT:
{context}

ANSWER:
{answer}

Write everything in English."""


def rule_checks(
    row: dict, answer: str, tools: list[str], context: str, retrieved: set[str]
) -> dict[str, bool]:
    """Cheap deterministic checks: no LLM, same result on every run."""
    kind = row["type"]
    refused = answer.strip() == Agent.REFUSAL
    if kind == "out_of_scope":
        return {"refused": refused, "no_tools": not tools}

    checks = {"not_refused": not refused}
    if kind != "no_answer":
        checks["used_tools"] = bool(tools)
    if kind == "photo":
        checks["called_find_photos"] = "find_photos" in tools
    if row.get("expected_sources"):
        checks["source_retrieved"] = any(
            s in retrieved for s in row["expected_sources"]
        )
    for term in row.get("must_include", []):
        checks[f"has:{term}"] = term.lower() in answer.lower()
    # code must be copied from the notes, not translated or rewritten (whitespace is ignored;
    # a block split between two chunks can give a false failure: check those by hand)
    blocks = [b for b in CODE_RE.findall(answer) if b.strip()]
    if blocks:
        ctx = _squash(context)
        checks["code_verbatim"] = all(_squash(b) in ctx for b in blocks)
    # every URL in the answer must come from a tool result: no invented images or links
    checks["no_invented_urls"] = all(u in context for u in URL_RE.findall(answer))
    # the prompt says "ALWAYS answer in English" (code blocks are removed before counting)
    prose = re.sub(r"```.*?```", "", answer, flags=re.S)
    letters = sum(c.isalpha() for c in prose) or 1
    checks["english"] = len(CYR_RE.findall(prose)) / letters < 0.05
    if kind in ("no_answer", "partial"):
        checks["admits_no_info"] = any(p in answer.lower() for p in NO_INFO)
    return checks


async def run_one(agent: Agent, judge, row: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        state_in, config = agent._run_args(
            row["question"], f"eval-{uuid.uuid4()}", "eval", "127.0.0.1", source="eval"
        )
        config["metadata"]["eval_case"] = row["id"]  # find the trace of a failed case by its id
        t0 = time.perf_counter()
        state = await agent.graph.ainvoke(state_in, config=config)
        latency = time.perf_counter() - t0

        msgs = state["messages"]
        answer = msgs[-1].content
        tools = [
            c["name"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls
        ]
        context = "\n\n".join(m.content for m in msgs if isinstance(m, ToolMessage))
        retrieved = set(SOURCE_RE.findall(context))
        tokens = sum(
            (m.usage_metadata or {}).get("total_tokens", 0)
            for m in msgs
            if isinstance(m, AIMessage)
        )

        checks = rule_checks(row, answer, tools, context, retrieved)
        verdict = None
        # the judge sees no_answer/partial too: that is where over-inference (hallucination) hides
        if row["type"] != "out_of_scope" and checks.get("not_refused"):
            verdict = await judge.ainvoke(
                JUDGE_PROMPT.format(
                    question=row["question"],
                    reference=row.get("reference")
                    or ", ".join(row.get("must_include", []))
                    or "-",
                    context=context or "(no tool results)",
                    answer=answer,
                ),
                config={
                    "callbacks": tracer_callbacks(),
                    "run_name": "judge",
                    "tags": ["eval", "judge"],
                    "metadata": {"eval_case": row["id"]},
                },
            )
        return {
            "id": row["id"],
            "type": row["type"],
            "question": row["question"],
            "answer": answer,
            "tools": tools,
            "retrieved": sorted(retrieved),
            "latency_s": round(latency, 2),
            "tokens": tokens,
            "checks": checks,
            "verdict": verdict.model_dump() if verdict else None,
        }


async def main() -> int:
    rows = [
        json.loads(line)
        for line in (HERE / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # booking is NOT passed: the eval must never create real calendar events
    agent = Agent(settings, VectorStoreService(settings.COLLECTION_NAME, settings))
    judge = ChatOpenAI(
        model=JUDGE_MODEL,
        api_key=settings.OPEN_ROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    ).with_structured_output(Verdict)
    sem = asyncio.Semaphore(3)
    results = await asyncio.gather(*(run_one(agent, judge, r, sem) for r in rows))

    all_checks = [v for r in results for v in r["checks"].values()]
    verdicts = [r["verdict"] for r in results if r["verdict"]]
    lat = sorted(r["latency_s"] for r in results)

    def avg(key):
        return round(statistics.mean(v[key] for v in verdicts), 2) if verdicts else None

    summary = {
        "collection": settings.COLLECTION_NAME,
        "n": len(results),
        "rules_pass_rate": round(sum(all_checks) / len(all_checks), 3),
        "faithfulness": avg("faithfulness"),
        "relevance": avg("relevance"),
        "correctness": avg("correctness"),
        "latency_p50_s": lat[len(lat) // 2],
        "latency_p95_s": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        "avg_tokens": round(statistics.mean(r["tokens"] for r in results)),
    }

    out = HERE / "results" / f"agent_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {"summary": summary, "results": results}, ensure_ascii=False, indent=2
        )
    )

    for r in results:
        failed = [k for k, ok in r["checks"].items() if not ok]
        low = (
            r["verdict"]
            and min(r["verdict"]["faithfulness"], r["verdict"]["relevance"]) < 4
        )
        if failed or low:
            print(f"FAIL {r['id']} [{r['type']}] {r['question'][:60]}")
            if failed:
                print(f"     checks: {failed}")
            if low:
                print(f"     judge: {r['verdict']['reasoning'][:200]}")
    print(json.dumps(summary, indent=2))
    print(f"report: {out}")

    bad = [
        k for k, t in THRESHOLDS.items() if summary[k] is not None and summary[k] < t
    ]
    if bad:
        print(f"BELOW THRESHOLD: {bad}")
    critical = [
        r["id"]
        for r in results
        if r["type"] in CRITICAL_TYPES
        and (
            not all(r["checks"].values())
            or (r["verdict"] and r["verdict"]["faithfulness"] < 4)
        )
    ]
    if critical:
        print(f"CRITICAL FAILURES: {critical}")
    flush_traces()  # a short script exits before the background thread uploads the traces
    # non-zero exit = a CI gate: a prompt/chunking change that makes things worse fails the build
    return 1 if bad or critical else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
