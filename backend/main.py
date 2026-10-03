from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.settings import settings
from routes.routes import router

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)

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
