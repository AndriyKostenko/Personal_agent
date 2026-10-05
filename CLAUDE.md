# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

"AI Obsidian Mentor": a RAG app that answers questions from an Obsidian vault. Monorepo with a FastAPI backend (`backend/`, Python 3.14, managed with `uv`) and a Vue 3 + Vite + TypeScript frontend (`frontend/`). No tests or linters are configured.

## Commands

Backend (run from `backend/`; imports are top-level, e.g. `from core.settings import ...`, so cwd must be `backend/`):
- Install: `uv sync`
- Run API: `uvicorn main:app --reload` (port 8000; API under `/api/v1`)
- Sync Obsidian notes into the vector DB (API must be running): `cd backend/pipeline && python sync_notes.py` (state file `sync_state.json` is resolved relative to cwd, so run from `pipeline/` to use the committed one)

Frontend (run from `frontend/`):
- `npm install`, `npm run dev`, `npm run build` (type-check + build), `npm run type-check`

## Architecture

Flow: Obsidian `.md` files → `pipeline/sync_notes.py` → `POST /api/v1/index` → embeddings → local Qdrant → `POST /api/v1/ask_mentor` → Vue chat UI (`frontend/src/App.vue`, single component, calls the URL in `frontend/.env` `BACKEND_ASK_API`).

Backend layers: `routes/routes.py` (endpoints: `/index`, `/search`, `/ask_mentor`) → `dependencies/dependencies.py` (DI; `VectorStoreService` is a module-level singleton because embedded Qdrant allows only one client per storage path) → `services/`:
- `vector_service.py`: embedded Qdrant (`QdrantClient(path=...)`, no server), collection `knowledge_base`, 1536-dim cosine. Embeddings via OpenRouter `openai/text-embedding-3-small` over raw httpx. Each `add_documents` call embeds one text and upserts with a random UUID (no upsert-by-key).
- `rag_service.py`: searches top-5 chunks, builds a context prompt, and calls OpenRouter (`gpt-4o-mini`) through `instructor` to get a structured `FactCheckingAnswer` (thought_process, is_context_sufficient, answer, used_sources). Schemas are in `schemas/schemas.py`.

Booking (optional): `services/calendar_service.py` talks to Google Calendar over httpx with an OAuth refresh token (free/busy -> free slots; event insert with invite + Meet link). `agent/tools.py` registers `check_availability` and `book_call` only when all three `GOOGLE_*` settings are set. The agent prompt gets a booking section with the current date, `book_call` must follow an explicit visitor confirmation (prompt rule), and per-visitor/IP/day booking limits live in the `bookings` table of the usage SQLite DB (visitor identity reaches the tool through the run config, set by the routes, never by the LLM). Get the refresh token once with `python utils/google_consent.py` (from `backend/`). `frontend/public/privacy.html` (`/privacy`) exists for the Google consent screen.

Sync pipeline: walks `OBSIDIAN_KNOWLEDGE_BASE_PATH` for `*.md`, strips/parses YAML front matter into metadata, splits with `RecursiveCharacterTextSplitter` (1000/150), posts each chunk, and skips files whose MD5 matches `sync_state.json`.

## Gotchas

- Booking: `BOOKING_TIMEZONE` must be an IANA name (`Etc/GMT+6` means UTC-6). Defaults are duplicated in `docker-compose.yml`. The OAuth app must be "In production" (a "Testing" app's refresh token expires after 7 days).

- Config comes from `backend/.env` via `core/settings.py` (`OPEN_ROUTER_API_KEY`, etc.); `OBSIDIAN_KNOWLEDGE_BASE_PATH` defaults to a hardcoded local path. Never commit `.env` files.
- `QDRANT_STORAGE_PATH` is relative (`./qdrant_db`), so the DB location depends on cwd when starting uvicorn. Both `backend/qdrant_db/` and `backend/utils/qdrant_db/` exist; `utils/view_db.py` is an inspection helper.
- Re-syncing a changed file adds new chunks without deleting the old ones (duplicates), since points get random UUIDs.
- `rag_service.generate_answer` returns only `{"answer": ...}` when no context is found, but `/ask_mentor` indexes `result["sources"]` etc., which would raise KeyError (surfaced as a 500).
- `sync_notes.py` reads `API_INDEX_URL` via `getattr`, which doesn't match the `INDEX_API_URL` setting, so the default URL is always used.
