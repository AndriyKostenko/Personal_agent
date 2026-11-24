from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.log_config import setup_logging
from core.settings import settings

# BEFORE importing the routes: that import builds the agent and the vector store (dependencies.py),
# and whatever they log while starting must already go through our handlers and format
setup_logging(settings.LOG_LEVEL, settings.log_file_path)

from core.tracing import flush_traces  # noqa: E402
from routes.routes import router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    flush_traces()  # traces are uploaded by a background thread: send what is still queued


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Client-Id"],
)


@app.get("/")
async def root():
    return {"message": "Hello from ai-mentor!"}


app.include_router(router, prefix="/api/v1")

# только папка с картинками, которую наполняет sync_notes.py (не хранилище Obsidian!)
Path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")

if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1", port=8000
    )  # 0.0.0.0 не нужен для локальной разработки
