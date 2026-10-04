import asyncio
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from core.settings import Settings


class LimitExceeded(Exception):
    """A visitor ran out of questions / chats. `code` tells the client which limit it was."""

    def __init__(self, code: str, message: str, usage: dict):
        super().__init__(message)
        self.code = code
        self.message = message
        self.usage = usage


@dataclass(frozen=True)
class Reservation:
    usage: dict  # the counters after this question was counted
    new_chat: bool  # this question opened a new chat (needed to undo it on a failure)


class UsageService:
    """Counts questions and chats per visitor (X-Client-Id) and questions per IP.

    Everything lives in one SQLite file, so the counters survive restarts and are shared
    by all worker processes of one container. Every check-and-count runs inside a single
    `BEGIN IMMEDIATE` transaction, so two parallel requests can not both take the last
    question. With several containers each one needs the same file (a shared volume) or the
    storage has to move to a network database (Redis, Firestore, Cloud SQL).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.USAGE_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # WAL: readers do not block the writer. It can not be switched inside a transaction.
        boot = sqlite3.connect(self.path, timeout=10)
        try:
            boot.execute("PRAGMA journal_mode=WAL")
        finally:
            boot.close()
        with self._tx() as conn:
            # one statement per execute(): executescript() would end the transaction itself
            conn.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    client_id  TEXT PRIMARY KEY,
                    questions  INTEGER NOT NULL DEFAULT 0,
                    created_at REAL,
                    updated_at REAL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS chats (
                    client_id  TEXT NOT NULL,
                    thread_id  TEXT NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (client_id, thread_id))"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ips (
                    ip         TEXT PRIMARY KEY,
                    questions  INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL)"""
            )

    # ---------- plumbing ----------

    @contextmanager
    def _tx(self):
        # a short-lived connection per call: safe for threads and for several processes
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def _snapshot(self, conn: sqlite3.Connection, client_id: str) -> dict:
        row = conn.execute("SELECT questions FROM users WHERE client_id = ?", (client_id,)).fetchone()
        chats = conn.execute("SELECT COUNT(*) FROM chats WHERE client_id = ?", (client_id,)).fetchone()[0]
        return {
            "questions_used": row[0] if row else 0,
            "questions_limit": self.settings.MAX_QUESTIONS_PER_USER,
            "chats_used": chats,
            "chats_limit": self.settings.MAX_CHATS_PER_USER,
        }

    # ---------- synchronous core (called through asyncio.to_thread) ----------

    def _usage(self, client_id: str) -> dict:
        with self._tx() as conn:
            return self._snapshot(conn, client_id)

    def _reserve(self, client_id: str, ip: str, thread_id: str | None) -> Reservation:
        s = self.settings
        now = time.time()
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO users (client_id, created_at) VALUES (?, ?)", (client_id, now))
            conn.execute("INSERT OR IGNORE INTO ips (ip) VALUES (?)", (ip,))
            usage = self._snapshot(conn, client_id)
            ip_questions = conn.execute("SELECT questions FROM ips WHERE ip = ?", (ip,)).fetchone()[0]

            if usage["questions_used"] >= s.MAX_QUESTIONS_PER_USER:
                raise LimitExceeded(
                    "question_limit",
                    f"You have used all {s.MAX_QUESTIONS_PER_USER} questions available to you.",
                    usage,
                )
            if ip_questions >= s.MAX_QUESTIONS_PER_IP:
                raise LimitExceeded(
                    "ip_limit",
                    "The question limit for your network has been reached.",
                    usage,
                )

            new_chat = False
            if thread_id is not None:
                known = conn.execute(
                    "SELECT 1 FROM chats WHERE client_id = ? AND thread_id = ?", (client_id, thread_id)
                ).fetchone()
                if not known:
                    if usage["chats_used"] >= s.MAX_CHATS_PER_USER:
                        raise LimitExceeded(
                            "chat_limit",
                            "Only one conversation is available per visitor. "
                            "Continue in your existing conversation.",
                            usage,
                        )
                    conn.execute(
                        "INSERT INTO chats (client_id, thread_id, created_at) VALUES (?, ?, ?)",
                        (client_id, thread_id, now),
                    )
                    new_chat = True

            conn.execute(
                "UPDATE users SET questions = questions + 1, updated_at = ? WHERE client_id = ?", (now, client_id)
            )
            conn.execute("UPDATE ips SET questions = questions + 1, updated_at = ? WHERE ip = ?", (now, ip))
            return Reservation(usage=self._snapshot(conn, client_id), new_chat=new_chat)

    def _refund(self, client_id: str, ip: str, thread_id: str | None, new_chat: bool) -> dict:
        """Undo a reservation: the question failed through no fault of the visitor."""
        with self._tx() as conn:
            conn.execute("UPDATE users SET questions = MAX(questions - 1, 0) WHERE client_id = ?", (client_id,))
            conn.execute("UPDATE ips SET questions = MAX(questions - 1, 0) WHERE ip = ?", (ip,))
            if new_chat and thread_id is not None:
                conn.execute("DELETE FROM chats WHERE client_id = ? AND thread_id = ?", (client_id, thread_id))
            return self._snapshot(conn, client_id)

    # ---------- async API ----------

    async def get_usage(self, client_id: str) -> dict:
        return await asyncio.to_thread(self._usage, client_id)

    async def reserve(self, client_id: str, ip: str, thread_id: str | None) -> Reservation:
        """Counts a question (and a new chat) or raises LimitExceeded. Call BEFORE the LLM runs."""
        return await asyncio.to_thread(self._reserve, client_id, ip, thread_id)

    async def refund(self, client_id: str, ip: str, thread_id: str | None, new_chat: bool) -> dict:
        return await asyncio.to_thread(self._refund, client_id, ip, thread_id, new_chat)
