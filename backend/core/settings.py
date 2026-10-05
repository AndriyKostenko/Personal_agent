from pydantic_settings import BaseSettings
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Obsidian Mentor"
    VERSION: str = "0.1.0"
    OPENROUTER_API_URL: str 
    OBSIDIAN_KNOWLEDGE_BASE_PATH: str = "/Users/andriykostenko/Documents/Obsidian Vault"
    OPEN_ROUTER_API_KEY: str
    LOCAL_BASE_URL: str = ""
    QDRANT_STORAGE_PATH: str = "./qdrant_db"
    COLLECTION_NAME: str = "knowledge_base_lc"
    OPENROUTER_EMBEDDINGS_URL: str = "https://openrouter.ai/api/v1/embeddings"
    OPENROUTER_CHAT_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    INDEX_API_URL: str = "http://127.0.0.1:8000/api/v1/index"
    MEDIA_DIR: str = str(
        Path(__file__).resolve().parent.parent / "media"
    )  # не зависит от cwd
    MEDIA_BASE_URL: str = "http://127.0.0.1:8000/media"
    VISION_MODEL: str = "openai/gpt-4o-mini"  # любая vision-модель OpenRouter
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    PHOTO_FOLDERS: list[str] = ["About me"]  # vault folders with photos not linked from notes

    # ───────────── usage limits (enforced on the server) ─────────────
    MAX_QUESTIONS_PER_USER: int = 10  # questions in total per visitor
    MAX_CHATS_PER_USER: int = 1  # chats (agent memory threads) per visitor
    # safety net against a visitor who clears the browser storage to get a new identity
    MAX_QUESTIONS_PER_IP: int = 15
    MAX_MESSAGE_CHARS: int = 1000
    # SQLite file with the counters: mount a persistent volume here when running in Docker
    USAGE_DB_PATH: str = str(BACKEND_DIR / "data" / "usage.db")
    # How many reverse proxies sit in front of the app and append to X-Forwarded-For
    # (0 = none: the socket address is used and the header is ignored, so it cannot be spoofed).
    TRUSTED_PROXY_COUNT: int = 0
    # protects /index and /search, which must not be public; empty = open (local development)
    ADMIN_API_KEY: str = ""

    # ───────────── booking via Google Calendar ─────────────
    # OAuth user credentials; get the refresh token with `python utils/google_consent.py`.
    # Any of the three empty = booking is disabled and the booking tools are not registered.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""
    CALENDAR_ID: str = "primary"
    BOOKING_TIMEZONE: str = "Etc/GMT+6"  # IANA name (this is UTC-6, no DST); timezone of the working hours
    WORK_DAYS: list[int] = [0, 1, 2, 3, 4]  # Monday = 0
    WORK_START_HOUR: int = 10
    WORK_END_HOUR: int = 18
    SLOT_MINUTES: int = 30
    BUFFER_MINUTES: int = 10  # free gap required around every existing event
    MIN_NOTICE_HOURS: int = 24
    BOOKING_HORIZON_DAYS: int = 30
    BOOKING_MEET_LINK: bool = True  # attach a Google Meet link to the event
    MAX_BOOKINGS_PER_USER: int = 1  # not enforced yet (abuse limits are a later step)

    @property
    def booking_enabled(self) -> bool:
        return bool(
            self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET and self.GOOGLE_REFRESH_TOKEN
        )

    class Config:
        env_file = BACKEND_DIR / ".env"


settings = Settings()
