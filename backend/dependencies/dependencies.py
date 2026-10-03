import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from core.settings import settings
from services.vector_service import VectorStoreService
from services.rag_service import RagService
from agent.agent import Agent
from services.usage_service import UsageService

# creating a singleton to garantee that qdrant_db will be obtained only once
vectore_store_instance = VectorStoreService(
    collection_name=settings.COLLECTION_NAME, settings=settings
)
agent_instance = Agent(settings, vectore_store_instance)
usage_instance = UsageService(settings)


def get_agent():
    return agent_instance


def get_vector_service():
    return vectore_store_instance


def get_rag_service(vector_service: VectorStoreService = Depends(get_vector_service)):
    return RagService(vector_service, settings)


vector_service_dependency = Annotated[VectorStoreService, Depends(get_vector_service)]
rag_service_dependency = Annotated[RagService, Depends(get_rag_service)]
agent_dependency = Annotated[Agent, Depends(get_agent)]


def get_usage_service():
    return usage_instance


usage_dependency = Annotated[UsageService, Depends(get_usage_service)]


# ───────────── who is asking ─────────────
@dataclass(frozen=True)
class ClientContext:
    client_id: str  # random id the browser generated once (X-Client-Id)
    ip: str  # address used as a second, harder to change, identity


def resolve_client_ip(request: Request) -> str:
    """The visitor's address. X-Forwarded-For is trusted only for the configured number of
    reverse proxies, and only the entry that our own proxy added (counting from the right)
    is used: everything on the left of it was written by the client and can be forged."""
    hops = settings.TRUSTED_PROXY_COUNT
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def get_client_context(request: Request, x_client_id: Annotated[str | None, Header()] = None) -> ClientContext:
    try:
        client_id = str(uuid.UUID(x_client_id or ""))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_client_id", "message": "Missing or invalid X-Client-Id header."},
        )
    return ClientContext(client_id=client_id, ip=resolve_client_ip(request))


client_dependency = Annotated[ClientContext, Depends(get_client_context)]


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """/index and /search are for the sync script only. Empty ADMIN_API_KEY = open (local dev)."""
    expected = settings.ADMIN_API_KEY
    if expected and not secrets.compare_digest(x_admin_key or "", expected):
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Admin key required."})
