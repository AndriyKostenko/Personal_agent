import asyncio
import json
import logging
import time
from uuid import uuid7

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

from core.log_config import request_id_var
from core.tracing import send_feedback

from dependencies.dependencies import (
    agent_dependency,
    client_dependency,
    rag_service_dependency,
    require_admin,
    usage_dependency,
    vector_service_dependency,
)
from schemas.schemas import (
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    IndexRequest,
    IndexResposne,
    QueryRequest,
    SearchResponse,
    IndexDocumentRequest,
    FeedbackRequest,
)
from services.chat_memory import thread_key
from services.usage_service import LimitExceeded

# the module name ("routes.routes"), like every other module: one naming rule for the whole app
logger = logging.getLogger(__name__)

router = APIRouter()

# Internal errors (they can contain provider details and ids) never reach the visitor.
FAILED_MESSAGE = (
    "The assistant could not answer right now. This question was not counted."
)


def _limit_error(e: LimitExceeded) -> HTTPException:
    # INFO, not WARNING: a limit is the system working as designed; the code shows which one
    logger.info("limit reached: %s", e.code)
    return HTTPException(
        status_code=429,
        detail={"code": e.code, "message": e.message, "usage": e.usage},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ───────────── admin: filling the knowledge base (used by pipeline/sync_notes.py) ─────────────
@router.post(
    "/index", response_model=IndexResposne, dependencies=[Depends(require_admin)]
)
async def index_text(data: IndexRequest, vector_service: vector_service_dependency):
    try:
        full_metadata = {"source": data.source, **data.metadata}
        await vector_service.add_documents(
            [{"text": data.text, "metadata": full_metadata}]
        )
        return IndexResposne(status="success", message="Text indexed successfully")
    except Exception as e:
        logger.exception("indexing failed (source=%s)", data.source)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/search", response_model=SearchResponse, dependencies=[Depends(require_admin)]
)
async def search_text(data: QueryRequest, vector_service: vector_service_dependency):
    try:
        results = await vector_service.search(data.query)
        return SearchResponse(
            status="success", message="Search results", results=results
        )
    except Exception as e:
        logger.exception("search failed")
        raise HTTPException(status_code=500, detail=str(e))


# ───────────── visitors ─────────────
@router.get("/usage")
async def get_usage(
    client: client_dependency, usage_service: usage_dependency, agent: agent_dependency
):
    """The visitor's counters (questions and chats used and their limits) and whether booking is on."""
    usage = await usage_service.get_usage(client.client_id)
    return {**usage, "booking_enabled": agent.booking_enabled}


@router.post("/ask_mentor", response_model=AskResponse)
async def ask_mentor(
    data: AskRequest,
    rag_service: rag_service_dependency,
    client: client_dependency,
    usage_service: usage_dependency,
):
    # it spends the same LLM budget, so it is counted like a chat question (but opens no chat)
    try:
        reservation = await usage_service.reserve(client.client_id, client.ip, None)
    except LimitExceeded as e:
        raise _limit_error(e)
    try:
        result = await rag_service.generate_answer(question=data.question)
        return AskResponse(
            answer=result["answer"],
            sources=result["sources"],
            thought_process=result["thought_process"],
            is_context_sufficient=result["is_context_sufficient"],
        )
    except Exception:
        logger.exception("ask_mentor failed")
        await usage_service.refund(
            client.client_id, client.ip, None, reservation.new_chat
        )
        raise HTTPException(
            status_code=500, detail={"code": "failed", "message": FAILED_MESSAGE}
        )


@router.post("/chat/stream")
async def chat_stream(
    data: ChatRequest,
    agent: agent_dependency,
    client: client_dependency,
    usage_service: usage_dependency,
):
    """Server-Sent Events: the agent's steps as they happen, then the final answer.

    The limits are checked BEFORE the stream starts, so a refused question gets a plain
    HTTP 429 and never reaches the LLM."""
    # one id for the whole request: every log line, the LangSmith trace and the feedback row
    run_id = uuid7()
    request_id_var.set(str(run_id))
    thread_id = str(data.thread_id)
    try:
        reservation = await usage_service.reserve(
            client.client_id, client.ip, thread_id
        )
    except LimitExceeded as e:
        raise _limit_error(e)

    # the agent's memory is keyed by visitor + chat, so nobody can read another visitor's thread
    memory_key = thread_key(client.client_id, thread_id)

    async def events():
        # the body is iterated by Starlette in a task created after this function returned;
        # it copies the context, but setting it here too keeps the id independent of that detail
        request_id_var.set(str(run_id))
        started = time.perf_counter()
        yield _sse({"type": "usage", "usage": reservation.usage})
        answered = False
        try:
            async for event in agent.astream_steps(
                data.message, memory_key, client.client_id, client.ip, run_id=run_id
            ):
                if event["type"] == "answer":
                    # BEFORE the answer reaches the browser: a fast click on 👍 must find the row
                    await usage_service.record_run(str(run_id), client.client_id)
                    answered = True
                yield _sse(event)
            if not answered:
                raise RuntimeError("the agent finished without an answer")
            logger.info("chat answered in %.1fs", time.perf_counter() - started)
        except Exception:
            # A failure that is not the visitor's fault gives the question back.
            # (A visitor who just closes the page is not refunded: the LLM has been paid for.)
            logger.exception("chat stream failed")
            usage = await usage_service.refund(
                client.client_id, client.ip, thread_id, reservation.new_chat
            )
            yield _sse({"type": "usage", "usage": usage})
            yield _sse({"type": "error", "message": FAILED_MESSAGE})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    data: ChatRequest,
    agent: agent_dependency,
    client: client_dependency,
    usage_service: usage_dependency,
):
    run_id = uuid7()
    request_id_var.set(str(run_id))
    thread_id = str(data.thread_id)
    try:
        reservation = await usage_service.reserve(
            client.client_id, client.ip, thread_id
        )
    except LimitExceeded as e:
        raise _limit_error(e)
    try:
        started = time.perf_counter()
        answer = await agent.ainvoke(
            data.message,
            thread_key(client.client_id, thread_id),
            client.client_id,
            client.ip,
            run_id=run_id,
        )
        await usage_service.record_run(str(run_id), client.client_id)
        logger.info("chat answered in %.1fs", time.perf_counter() - started)
        return ChatResponse(answer=answer, usage=reservation.usage, run_id=str(run_id))
    except Exception:
        logger.exception("chat failed")
        await usage_service.refund(
            client.client_id, client.ip, thread_id, reservation.new_chat
        )
        raise HTTPException(
            status_code=500, detail={"code": "failed", "message": FAILED_MESSAGE}
        )


@router.post("/feedback", status_code=204)
async def feedback(
    data: FeedbackRequest, client: client_dependency, usage_service: usage_dependency
):
    """👍/👎 on one answer: stored in the usage DB, then sent to the LangSmith trace."""
    request_id_var.set(str(data.run_id))  # the log lines of the rating join the run's lines
    if not await usage_service.claim_feedback(
        str(data.run_id), client.client_id, data.score
    ):
        # one answer for "not your run", "unknown run" and "already rated": the endpoint
        # does not tell anyone which run ids exist
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "Unknown answer."}
        )
    logger.info("feedback: score=%d", data.score)
    try:
        # create_feedback is a blocking HTTP call: off the event loop
        await asyncio.to_thread(send_feedback, data.run_id, data.score, data.comment)
    except Exception:
        # the rating is saved locally; LangSmith being down is not the visitor's problem
        logger.exception("feedback upload to LangSmith failed")
    return Response(status_code=204)


@router.post(
    "/index/document",
    response_model=IndexResposne,
    dependencies=[Depends(require_admin)],
)
async def index_document(
    data: IndexDocumentRequest, vector_service: vector_service_dependency
):
    docs = [
        {"text": c.text, "metadata": {**c.metadata, "source": data.source}}
        for c in data.chunks
    ]
    try:
        await vector_service.replace_source(data.source, docs)
    except Exception:
        logger.exception("Document indexing failed (source=%s)", data.source)
        raise HTTPException(status_code=500, detail="indexing failed")
    return IndexResposne(status="success", message=f"{len(docs)} chunks indexed")
