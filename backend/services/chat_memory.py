"""The agent's chat memory: where it lives and when it is forgotten.

The memory is LangGraph's checkpointer (AsyncSqliteSaver, CHECKPOINT_DB_PATH). A checkpointer
keeps the whole checkpoint history of a thread, not only its latest state, and never deletes
anything by itself. So a TTL job deletes the threads that had no question for
CHAT_MEMORY_TTL_DAYS; the usage DB (`chats.last_active_at`) tells which ones.

A deleted thread is not an error for the visitor: the chat goes on, the agent just starts
from an empty memory (as after a restart before the memory was persistent)."""

import asyncio
import logging
import time

from langgraph.checkpoint.base import BaseCheckpointSaver

from services.usage_service import UsageService

log = logging.getLogger(__name__)

CLEANUP_EVERY_S = 3600  # the TTL is in days: once an hour is precise enough


def thread_key(client_id: str, thread_id: str) -> str:
    """The checkpointer's thread id. Keyed by visitor + chat, so nobody can read another
    visitor's thread by sending its thread_id. The routes and the cleanup must build it the
    same way, so it lives here only."""
    return f"{client_id}:{thread_id}"


async def purge_expired_chats(
    saver: BaseCheckpointSaver, usage: UsageService, ttl_days: int
) -> int:
    """Delete the memory of every chat idle for ttl_days. Returns how many were deleted."""
    cutoff = time.time() - ttl_days * 86400
    deleted = 0
    for client_id, thread_id in await usage.expired_chats(cutoff):
        # memory first, the mark second: if the process dies in between, the next run
        # deletes the (already empty) thread again, which is harmless
        await saver.adelete_thread(thread_key(client_id, thread_id))
        await usage.mark_memory_deleted(client_id, thread_id)
        deleted += 1
    return deleted


async def cleanup_loop(saver: BaseCheckpointSaver, usage: UsageService, ttl_days: int) -> None:
    """Runs for the life of the API process (started and cancelled in main.py: lifespan)."""
    while True:
        try:
            if deleted := await purge_expired_chats(saver, usage, ttl_days):
                log.info("chat memory: deleted %d chat(s) idle for %d+ days", deleted, ttl_days)
        except Exception:
            # one failed run must not stop the cleanup for good: try again in an hour
            log.exception("chat memory cleanup failed")
        await asyncio.sleep(CLEANUP_EVERY_S)
