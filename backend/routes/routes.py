import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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
)
from services.usage_service import LimitExceeded

logger = logging.getLogger("ai_mentor")

router = APIRouter()

# Internal errors (they can contain provider details and ids) never reach the visitor.
FAILED_MESSAGE = "The assistant could not answer right now. This question was not counted."


def _limit_error(e: LimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": e.code, "message": e.message, "usage": e.usage},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ───────────── admin: filling the knowledge base (used by pipeline/sync_notes.py) ─────────────
@router.post("/index", response_model=IndexResposne, dependencies=[Depends(require_admin)])
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


@router.post("/search", response_model=SearchResponse, dependencies=[Depends(require_admin)])
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
async def get_usage(client: client_dependency, usage_service: usage_dependency, agent: agent_dependency):
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
        await usage_service.refund(client.client_id, client.ip, None, reservation.new_chat)
        raise HTTPException(status_code=500, detail={"code": "failed", "message": FAILED_MESSAGE})


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
    thread_id = str(data.thread_id)
    try:
        reservation = await usage_service.reserve(client.client_id, client.ip, thread_id)
    except LimitExceeded as e:
        raise _limit_error(e)

    # the agent's memory is keyed by visitor + chat, so nobody can read another visitor's thread
    thread_key = f"{client.client_id}:{thread_id}"

    async def events():
        yield _sse({"type": "usage", "usage": reservation.usage})
        answered = False
        try:
            async for event in agent.astream_steps(data.message, thread_key, client.client_id, client.ip):
                answered = answered or event["type"] == "answer"
                yield _sse(event)
            if not answered:
                raise RuntimeError("the agent finished without an answer")
        except Exception:
            # A failure that is not the visitor's fault gives the question back.
            # (A visitor who just closes the page is not refunded: the LLM has been paid for.)
            logger.exception("chat stream failed")
            usage = await usage_service.refund(client.client_id, client.ip, thread_id, reservation.new_chat)
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
    thread_id = str(data.thread_id)
    try:
        reservation = await usage_service.reserve(client.client_id, client.ip, thread_id)
    except LimitExceeded as e:
        raise _limit_error(e)
    try:
        answer = await agent.ainvoke(
            data.message, f"{client.client_id}:{thread_id}", client.client_id, client.ip
        )
        return ChatResponse(answer=answer, usage=reservation.usage)
    except Exception:
        logger.exception("chat failed")
        await usage_service.refund(client.client_id, client.ip, thread_id, reservation.new_chat)
        raise HTTPException(status_code=500, detail={"code": "failed", "message": FAILED_MESSAGE})
