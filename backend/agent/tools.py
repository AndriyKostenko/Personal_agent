import logging
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from services.calendar_service import BookingError, CalendarService
from services.usage_service import LimitExceeded, UsageService
from services.vector_service import VectorStoreService

log = logging.getLogger(__name__)


MIN_PHOTO_SCORE = 0.3 # the accuracy of the related fotos 

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_SLOTS_PER_DAY = 4  # spread over the day; keeps the tool result short for the model
CALENDAR_DOWN = "The calendar is temporarily unavailable. Tell the visitor to try again later."


def _spread(items: list, n: int) -> list:
    """n items evenly spread over the list (the whole list if it is short)."""
    if len(items) <= n:
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def _clean(text: str, limit: int) -> str:
    """Single-line, control-free, length-limited text for the event (the values come from the LLM)."""
    return " ".join("".join(c for c in text if c.isprintable() or c.isspace()).split())[:limit]


def build_tools(
    vector_service: VectorStoreService,
    calendar_service: CalendarService | None = None,
    usage_service: UsageService | None = None,
) -> list[Callable]:
    """One tool - one responsibility"""

    @tool
    async def search_about_me(query: str) -> str:
        """Search the personal knowledge base (notes about Andriy: biography,
        skills, projects, hobbies, education, work experience).
        Use it for ANY factual question. Pass a short, focused search query; for broad
        topics call it several times with different sub-topics. Results include
        the original code snippets from the notes."""
        results = await vector_service.search(query, limit=8)
        if not results:
            return "Nothing found"
        return "\n\n---\n\n".join(f"[source: {r['metadata'].get('source')}]\n{r['text']}" for r in results)

    @tool
    async def find_photos(query: str) -> str:
        """Find photos of Andriy or related to a topic (e.g. 'travel', 'conference',
        'workplace'). Returns image URLs with captions. Call it when the user asks to
        show/see a photo, or when a picture would clearly help the answer."""
        results = await vector_service.search(query, limit=8, only_images=True)
        lines = [
            f"- {r['metadata']['image_url']} | {r['text'].splitlines()[0]}"
            for r in results 
            if r["score"] >= MIN_PHOTO_SCORE
        ]
        return "\n".join(lines) or "No photos found"
 

    tools = [search_about_me, find_photos]
    if calendar_service is None or usage_service is None:
        return tools  # booking needs both the calendar and the limits

    @tool
    async def check_availability(date_from: str, date_to: str) -> str:
        """List Andriy's free time slots for booking a call. Dates are YYYY-MM-DD
        (inclusive, at most 14 days apart). Call it BEFORE proposing any time and offer the
        visitor only slots it returned. Each slot is an ISO timestamp; pass the exact value
        to book_call."""
        try:
            first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError:
            return "Dates must be in YYYY-MM-DD format."
        try:
            slots = await calendar_service.get_free_slots(first, last)
        except Exception:
            log.exception("check_availability failed")
            return CALENDAR_DOWN

        header = (
            f"Timezone: {calendar_service.tz_label()}. "
            f"Current time: {calendar_service.now():%Y-%m-%d %H:%M}. "
            f"Slot length: {calendar_service.settings.SLOT_MINUTES} min."
        )
        if not slots:
            return f"{header}\nNo free slots in this period. Try other dates."
        by_day: dict[date, list[datetime]] = defaultdict(list)
        for slot in slots:
            by_day[slot.date()].append(slot)
        lines = [
            f"- {day:%a %Y-%m-%d}: " + ", ".join(s.isoformat() for s in _spread(day_slots, MAX_SLOTS_PER_DAY))
            for day, day_slots in by_day.items()
        ]
        return header + "\n" + "\n".join(lines)

    @tool
    async def book_call(start: str, name: str, email: str, topic: str, config: RunnableConfig) -> str:
        """Book a call with Andriy and send the visitor a calendar invite. Call it ONLY after
        the visitor has explicitly confirmed the time, name and e-mail. `start` must be an exact
        ISO timestamp returned by check_availability."""
        name, topic = _clean(name, 80), _clean(topic, 300)
        email = email.strip()
        if not name or not topic:
            return "Name and topic are required. Ask the visitor for them."
        if not EMAIL_RE.match(email) or len(email) > 254:
            return "The e-mail address looks invalid. Ask the visitor to check it."
        try:
            when = datetime.fromisoformat(start.strip())
        except ValueError:
            return "Invalid start time. Use an exact ISO timestamp from check_availability."
        if when.tzinfo is None:
            return "The start time must include the UTC offset, exactly as check_availability returned it."
        # who is asking comes from the server (the routes put it into the run config), never from the LLM
        visitor = config.get("configurable", {})
        client_id, ip = visitor.get("client_id"), visitor.get("ip")
        if not client_id or not ip:
            return "Booking is not available in this conversation."
        try:
            reservation = await usage_service.reserve_booking(client_id, ip)
        except LimitExceeded as e:
            return f"{e.message} Do not try again; tell the visitor that the booking limit was reached."
        try:
            booking = await calendar_service.create_booking(when, name, email, topic)
        except BookingError as e:
            await usage_service.release_booking(reservation)
            return str(e)
        except Exception:
            log.exception("book_call failed")
            await usage_service.release_booking(reservation)
            return CALENDAR_DOWN

        result = (
            f"Booked: {booking['start']:%A %Y-%m-%d %H:%M} "
            f"({calendar_service.tz_label()}). An invite was sent to {email}."
        )
        if booking["meet"]:
            result += f" Google Meet link: {booking['meet']}"
        return result

    return [*tools, check_availability, book_call]
