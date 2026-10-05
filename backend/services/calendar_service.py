import asyncio
import hashlib
import logging
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import httpx

from core.settings import Settings

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/calendar/v3"
MAX_WINDOW_DAYS = 14  # one availability query never looks further than this


class BookingError(Exception):
    """A problem the visitor can be told about (the message is shown to the model as is)."""


class CalendarService:
    """Free/busy lookup and event creation on Andriy's Google Calendar (REST over httpx)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.tz = ZoneInfo(settings.BOOKING_TIMEZONE)
        self.slot = timedelta(minutes=settings.SLOT_MINUTES)
        self._token: str | None = None
        self._token_expires = 0.0  # time.monotonic() value
        self._token_lock = asyncio.Lock()
        # one booking at a time: re-check + insert must not interleave with another booking
        self._book_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=15)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def tz_label(self) -> str:
        """The current UTC offset as a visitor reads it, e.g. 'UTC-06:00'."""
        offset = self.now().strftime("%z")  # -0600
        return f"UTC{offset[:3]}:{offset[3:]}"

    # ---- Google API plumbing ----

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires - 60:
                return self._token
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "client_id": self.settings.GOOGLE_CLIENT_ID,
                    "client_secret": self.settings.GOOGLE_CLIENT_SECRET,
                    "refresh_token": self.settings.GOOGLE_REFRESH_TOKEN,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            payload = response.json()
            self._token = payload["access_token"]
            self._token_expires = time.monotonic() + payload.get("expires_in", 3600)
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = await self._access_token()
        return await self._client.request(
            method,
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )

    @property
    def _calendar_path(self) -> str:
        return f"/calendars/{self.settings.CALENDAR_ID}"

    # ---- availability ----

    async def _busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        calendar_id = self.settings.CALENDAR_ID
        response = await self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "timeZone": self.settings.BOOKING_TIMEZONE,
                "items": [{"id": calendar_id}],
            },
        )
        response.raise_for_status()
        calendar = response.json()["calendars"][calendar_id]
        if calendar.get("errors"):
            raise RuntimeError(f"freeBusy errors: {calendar['errors']}")
        return [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in calendar.get("busy", [])
        ]

    def _day_slots(self, day: date) -> list[datetime]:
        """Every slot of the working day, before any busy/notice filtering."""
        s = self.settings
        if day.weekday() not in s.WORK_DAYS:
            return []
        cursor = datetime.combine(day, dtime(s.WORK_START_HOUR), self.tz)
        close = datetime.combine(day, dtime(s.WORK_END_HOUR), self.tz)
        slots = []
        while cursor + self.slot <= close:
            slots.append(cursor)
            cursor += self.slot
        return slots

    async def get_free_slots(self, date_from: date, date_to: date) -> list[datetime]:
        """Free slots (booking timezone, ascending) for the date range, limited by the
        minimum notice, the booking horizon and a 14-day window."""
        s = self.settings
        now = self.now()
        earliest = now + timedelta(hours=s.MIN_NOTICE_HOURS)
        first = max(date_from, earliest.date())
        last = min(date_to, now.date() + timedelta(days=s.BOOKING_HORIZON_DAYS))
        last = min(last, first + timedelta(days=MAX_WINDOW_DAYS - 1))
        if last < first:
            return []

        window_start = datetime.combine(first, dtime.min, self.tz)
        window_end = datetime.combine(last + timedelta(days=1), dtime.min, self.tz)
        busy = await self._busy_intervals(window_start, window_end)
        buffer = timedelta(minutes=s.BUFFER_MINUTES)

        free = []
        for offset in range((last - first).days + 1):
            for start in self._day_slots(first + timedelta(days=offset)):
                end = start + self.slot
                if start < earliest:
                    continue
                if any(b_start - buffer < end and b_end + buffer > start for b_start, b_end in busy):
                    continue
                free.append(start)
        return free

    # ---- booking ----

    @staticmethod
    def _event_id(email: str, start: datetime) -> str:
        """Deterministic id (hex is valid for Google): a retried booking finds its own event."""
        return hashlib.sha1(f"{email.lower()}|{start.isoformat()}".encode()).hexdigest()

    async def create_booking(self, start: datetime, name: str, email: str, topic: str) -> dict:
        """Creates the event and e-mails the invite. `start` must be one of the free slots.
        Returns {"start": datetime, "link": str, "meet": str | None}."""
        start = start.astimezone(self.tz)
        event_id = self._event_id(email, start)

        async with self._book_lock:
            existing = await self._request("GET", f"{self._calendar_path}/events/{event_id}")
            if existing.status_code == 200 and existing.json().get("status") != "cancelled":
                return self._booking_result(start, existing.json())  # a retry: already booked

            if start not in await self.get_free_slots(start.date(), start.date()):
                raise BookingError(
                    "This slot is not available (taken, outside working hours or too soon). "
                    "Call check_availability and offer the visitor one of the returned slots."
                )

            body = {
                "id": event_id,
                "summary": f"Call with {name}",
                "description": f"Topic: {topic}\nBooked by {name} <{email}> via the AI assistant.",
                "start": {"dateTime": start.isoformat(), "timeZone": self.settings.BOOKING_TIMEZONE},
                "end": {
                    "dateTime": (start + self.slot).isoformat(),
                    "timeZone": self.settings.BOOKING_TIMEZONE,
                },
                "attendees": [{"email": email, "displayName": name}],
            }
            if self.settings.BOOKING_MEET_LINK:
                body["conferenceData"] = {
                    "createRequest": {
                        "requestId": event_id,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }
            response = await self._request(
                "POST",
                f"{self._calendar_path}/events",
                params={"sendUpdates": "all", "conferenceDataVersion": 1},
                json=body,
            )
            if response.status_code == 409:  # same id exists but was cancelled earlier
                raise BookingError("This slot cannot be booked for this e-mail. Offer another slot.")
            response.raise_for_status()
            return self._booking_result(start, response.json())

    @staticmethod
    def _booking_result(start: datetime, event: dict) -> dict:
        return {"start": start, "link": event.get("htmlLink", ""), "meet": event.get("hangoutLink")}
