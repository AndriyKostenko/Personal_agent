"""Logging for every process of the backend (the API, the sync job): call setup_logging() ONCE at start.

Modules do not import anything from here to log: they use the standard
    log = logging.getLogger(__name__)
so the logger names follow the packages (agent.tools, routes.routes, ...) and the libraries
(httpx, langchain) go through the same handlers.

request_id: the route sets it once per request (request_id_var.set(...)); every log line written
while that request runs gets it, also from graph nodes and tools, because asyncio tasks copy the
context they are created in. It is the same id as the LangSmith trace (run_id), so a log line
leads straight to its trace.

Never log visitor text (questions, answers, e-mails): ids, codes and numbers only. The texts are
in the traces, where core/tracing.py masks them."""

import logging
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
# chatty libraries: one INFO line per HTTP request (embeddings, LLM calls, LangSmith uploads),
# and langchain_openai's start-up note about the proxy settings
QUIET = ("httpx", "httpcore", "openai", "langsmith", "langchain_openai")


class RequestIdFilter(logging.Filter):
    """Adds record.request_id for FORMAT. Set on the HANDLERS, not on a logger: a logger's filter
    does not see the records that propagate up from child loggers (agent.tools -> root)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO", log_file: str = "") -> None:
    """stdout always (Docker collects and rotates it, see x-logging in docker-compose.yml);
    plus a rotating file when log_file is set (local runs). Calling it again replaces the handlers."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 5 MB x 3 files. One process only: two processes rotating the same file lose lines.
        handlers.append(
            RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        )

    formatter = logging.Formatter(FORMAT)
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    for old in root.handlers[:]:  # uvicorn --reload or a second call: no duplicated lines
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level.upper())

    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
