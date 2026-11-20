"""LangSmith tracing: explicit opt-in, PII scrubbed on the client before anything is sent.

Only runs that get `tracer_callbacks()` in their config are traced. LangChain's automatic tracer
(switched on by LANGSMITH_TRACING in os.environ) is turned off, so a call we forgot to wire can
never send unmasked visitor data."""

import hashlib
import os
import re
from functools import lru_cache
from typing import Any

from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client

from core.settings import settings

# no automatic (unmasked) tracer for every LangChain call
for _var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ.pop(_var, None)
# the SDK takes the trace destination (agent + environment) from os.environ; pydantic does not export .env
if settings.LANGSMITH_AGENT_ID and settings.LANGSMITH_AGENT_ENVIRONMENT:
    os.environ["LANGSMITH_AGENT_ID"] = settings.LANGSMITH_AGENT_ID
    os.environ["LANGSMITH_AGENT_ENVIRONMENT"] = settings.LANGSMITH_AGENT_ENVIRONMENT

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),  # visitors' e-mails (booking)
    (re.compile(r"sk-[\w-]{20,}"), "<API_KEY>"),
    (re.compile(r"Bearer\s+[\w.-]{20,}"), "Bearer <TOKEN>"),
]
_DROP_KEYS = {"ip"}  # LangGraph copies `configurable` into the trace metadata
_PSEUDONYM_KEYS = {"client_id", "thread_id"}  # stable hash: LangSmith Threads still group by it


def _pseudonym(value: str) -> str:
    return "anon-" + hashlib.sha256(value.encode()).hexdigest()[:16]


def scrub(data: Any, depth: int = 0) -> Any:
    """Applied by the client to inputs, outputs, metadata AND errors of every run.
    It is passed as `anonymizer`: when one is set, the SDK ignores hide_metadata, so one function does it all."""
    if depth > 20:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _DROP_KEYS:
                continue
            out[k] = _pseudonym(v) if k in _PSEUDONYM_KEYS and isinstance(v, str) else scrub(v, depth + 1)
        return out
    if isinstance(data, list):
        return [scrub(v, depth + 1) for v in data]
    if isinstance(data, str):
        for pattern, repl in _PATTERNS:
            data = pattern.sub(repl, data)
    return data


def tracing_enabled() -> bool:
    return settings.LANGSMITH_TRACING and bool(settings.LANGSMITH_API_KEY.get_secret_value())


@lru_cache(maxsize=1)
def _client() -> Client:
    # one shared client: it owns the HTTP session and the background upload thread
    return Client(
        api_url=settings.LANGSMITH_ENDPOINT,
        api_key=settings.LANGSMITH_API_KEY.get_secret_value(),
        anonymizer=scrub,
    )


def tracer_callbacks() -> list:
    """Put into a run's config["callbacks"]. A fresh tracer per run (it keeps per-run state)."""
    return [LangChainTracer(client=_client())] if tracing_enabled() else []


def flush_traces() -> None:
    """Send what is still queued: call before the process exits."""
    if tracing_enabled():
        _client().flush()
