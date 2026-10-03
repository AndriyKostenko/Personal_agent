from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IndexRequest(BaseModel):
    text: str
    source: str = "manual_input"
    metadata: dict[str, Any] | None = Field(default_factory=dict)

class IndexResposne(BaseModel):
    status: str
    message: str

class QueryRequest(BaseModel):
    query: str
    limit: int = 3

class SearchResponse(BaseModel):
    status: str
    message: str
    results: list

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

class AskResponse(BaseModel):
    thought_process: str = Field(
        description="The thought process used to answer the question."
    )
    is_context_sufficient: bool = Field(
        description="True, if the provided context fully answers the question. False, if there is insufficient information."
    )
    answer: str
    sources: list[str]

class FactCheckingAnswer(BaseModel):
    thought_process: str = Field(
        description="Check if the provided context contains facts to answer the question."
    )
    is_context_sufficient: bool = Field(
        description="True, if the provided context fully answers the question. False, if there is insufficient information."
    )
    answer: str = Field(
        description="The linked answer based on the context. If is_context_sufficient=False, be honest and say that there is no data in the knowledge base."
    )
    used_sources: list[str] = Field(
        default_factory=list,
        description="The list of names of the files used as sources for the answer."
    )
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)  # keep in sync with MAX_MESSAGE_CHARS
    thread_id: UUID  # one thread = one chat; the id is created by the client

class UsageInfo(BaseModel):
    questions_used: int
    questions_limit: int
    chats_used: int
    chats_limit: int


class ChatResponse(BaseModel):
    answer: str
    usage: UsageInfo | None = None
