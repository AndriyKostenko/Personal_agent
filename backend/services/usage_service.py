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
            # added later: an existing database gets the columns here (CREATE TABLE IF NOT EXISTS
            # does not change a table that is already there)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(chats)")}
            if "last_active_at" not in columns:
                # the last question in this chat: the memory TTL counts from it
                conn.execute("ALTER TABLE chats ADD COLUMN last_active_at REAL")
            if "memory_deleted_at" not in columns:
                # when services/chat_memory.py deleted the agent's memory of this chat (NULL = kept)
                conn.execute("ALTER TABLE chats ADD COLUMN memory_deleted_at REAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS bookings (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id  TEXT NOT NULL,
                    ip         TEXT NOT NULL,
                    created_at REAL NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS ips (
                    ip         TEXT PRIMARY KEY,
                    questions  INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL)"""
            )
            # one row per answered run: who may rate it, and the rating (NULL = not rated yet).
            # Kept here even without LangSmith: the ratings survive a disabled tracer.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id     TEXT PRIMARY KEY,
                    client_id  TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    feedback   INTEGER,
                    rated_at   REAL)"""
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
                if known:
                    # activity: the memory TTL starts again; a chat whose memory was deleted is
                    # remembered from now on again, so it can expire (and be deleted) once more
                    conn.execute(
                        """UPDATE chats SET last_active_at = ?, memory_deleted_at = NULL
                           WHERE client_id = ? AND thread_id = ?""",
                        (now, client_id, thread_id),
                    )
                else:
                    if usage["chats_used"] >= s.MAX_CHATS_PER_USER:
                        raise LimitExceeded(
                            "chat_limit",
                            "Only one conversation is available per visitor. "
                            "Continue in your existing conversation.",
                            usage,
                        )
                    conn.execute(
                        "INSERT INTO chats (client_id, thread_id, created_at, last_active_at) VALUES (?, ?, ?, ?)",
                        (client_id, thread_id, now, now),
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

    def _reserve_booking(self, client_id: str, ip: str) -> int:
        s = self.settings
        now = time.time()
        with self._tx() as conn:
            def count(where: str, *args) -> int:
                return conn.execute(f"SELECT COUNT(*) FROM bookings WHERE {where}", args).fetchone()[0]

            if count("client_id = ?", client_id) >= s.MAX_BOOKINGS_PER_USER:
                raise LimitExceeded(
                    "booking_limit",
                    "This visitor has already booked the maximum number of calls.",
                    {},
                )
            if count("ip = ?", ip) >= s.MAX_BOOKINGS_PER_IP:
                raise LimitExceeded(
                    "booking_limit", "The booking limit for this network has been reached.", {}
                )
            if count("created_at > ?", now - 86400) >= s.MAX_BOOKINGS_PER_DAY:
                raise LimitExceeded(
                    "booking_limit",
                    "Too many calls were booked today. Booking is paused, try again tomorrow.",
                    {},
                )
            cursor = conn.execute(
                "INSERT INTO bookings (client_id, ip, created_at) VALUES (?, ?, ?)", (client_id, ip, now)
            )
            return cursor.lastrowid

    def _release_booking(self, booking_id: int) -> None:
        """Undo a booking reservation whose event could not be created."""
        with self._tx() as conn:
            conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))

    def _expired_chats(self, cutoff: float) -> list[tuple[str, str]]:
        """(client_id, thread_id) of the chats with no question since `cutoff` whose memory is
        still kept. Chats from before last_active_at existed fall back to created_at."""
        with self._tx() as conn:
            return conn.execute(
                """SELECT client_id, thread_id FROM chats
                   WHERE COALESCE(last_active_at, created_at, 0) < ? AND memory_deleted_at IS NULL""",
                (cutoff,),
            ).fetchall()

    def _mark_memory_deleted(self, client_id: str, thread_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE chats SET memory_deleted_at = ? WHERE client_id = ? AND thread_id = ?",
                (time.time(), client_id, thread_id),
            )

    def _record_run(self, run_id: str, client_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO runs (run_id, client_id, created_at) VALUES (?, ?, ?)",
                (run_id, client_id, time.time()),
            )

    def _claim_feedback(self, run_id: str, client_id: str, score: int) -> bool:
        """One atomic UPDATE checks both rules: the run belongs to THIS visitor and has no rating
        yet. A check-then-write in two queries would let two parallel requests both pass."""
        with self._tx() as conn:
            cursor = conn.execute(
                """UPDATE runs SET feedback = ?, rated_at = ?
                   WHERE run_id = ? AND client_id = ? AND feedback IS NULL""",
                (score, time.time(), run_id, client_id),
            )
            return cursor.rowcount == 1

    # ---------- async API ----------

    async def get_usage(self, client_id: str) -> dict:
        return await asyncio.to_thread(self._usage, client_id)

    async def reserve(self, client_id: str, ip: str, thread_id: str | None) -> Reservation:
        """Counts a question (and a new chat) or raises LimitExceeded. Call BEFORE the LLM runs."""
        return await asyncio.to_thread(self._reserve, client_id, ip, thread_id)

    async def reserve_booking(self, client_id: str, ip: str) -> int:
        """Counts a booking or raises LimitExceeded. Call BEFORE creating the event."""
        return await asyncio.to_thread(self._reserve_booking, client_id, ip)

    async def release_booking(self, booking_id: int) -> None:
        await asyncio.to_thread(self._release_booking, booking_id)

    async def expired_chats(self, cutoff: float) -> list[tuple[str, str]]:
        """Chats idle since `cutoff` (a unix time) whose agent memory has not been deleted yet."""
        return await asyncio.to_thread(self._expired_chats, cutoff)

    async def mark_memory_deleted(self, client_id: str, thread_id: str) -> None:
        await asyncio.to_thread(self._mark_memory_deleted, client_id, thread_id)

    async def record_run(self, run_id: str, client_id: str) -> None:
        """Remember who got this answer, so only they can rate it. Call when the answer is ready."""
        await asyncio.to_thread(self._record_run, run_id, client_id)

    async def claim_feedback(self, run_id: str, client_id: str, score: int) -> bool:
        """Store the rating. False = not this visitor's run, unknown run, or already rated."""
        return await asyncio.to_thread(self._claim_feedback, run_id, client_id, score)

    async def refund(self, client_id: str, ip: str, thread_id: str | None, new_chat: bool) -> dict:
        return await asyncio.to_thread(self._refund, client_id, ip, thread_id, new_chat)
