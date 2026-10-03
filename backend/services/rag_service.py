from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from schemas.schemas import FactCheckingAnswer
from services.vector_service import VectorStoreService
from core.settings import Settings

SYSTEM_PROMPT = (
    "You are an experienced AI mentor.\n"
    "First, analyze the context in the 'thought_process' field.\n"
    "If the context has an answer, make 'is_context_sufficient=True' and fill the 'answer' field.\n"
    "CRITICAL INSTRUCTIONS FOR THE 'answer' FIELD:\n"
    "- Provide a detailed, comprehensive, and educational explanation.\n"
    "- You MUST preserve and include ALL code snippets, examples, and tables exactly as they appear in the context.\n"
    "- Format the answer using Markdown (use ``` for code blocks and specify the language, e.g., ```go).\n"
    "- DO NOT overly summarize the technical details. If the context contains code, show the code!\n"
    "If the context does not have an answer, make 'is_context_sufficient=False' and respond clearly."
)

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "user",
            "Context from the knowledge database: {context}\n\n Question from user: {question}",
        ),
    ]
)


class RagService:
    """
    Service for generating answers using RAG (Retrieval-Augmented Generation) approach.
    """

    def __init__(self, vector_service: VectorStoreService, settings: Settings) -> None:
        self.vector_service = vector_service
        self.settings = settings
        llm = ChatOpenAI(
            model="openai/gpt-4o-mini",
            api_key=self.settings.OPEN_ROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )
        # LCEL chain: prompt -> LLM that returns a validated FactCheckingAnswer
        self.chain = prompt | llm.with_structured_output(FactCheckingAnswer)

    async def generate_answer(self, question: str) -> dict[str, Any]:
        # 1. getting relevant chunks from Qdrant
        search_results = await self.vector_service.search(query=question)

        # 2. creating context
        context_blocks = [
            f"[Source: {res['metadata'].get('source', 'unknown')}]\n{res['text']}"
            for res in search_results
        ]
        if not context_blocks:
            return {
                "thought_process": "No documents were found in the knowledge base.",
                "answer": "No relevant context found.",
                "is_context_sufficient": False,
                "sources": [],
            }

        result: FactCheckingAnswer = await self.chain.ainvoke(
            {"context": "\n\n---\n\n".join(context_blocks), "question": question}
        )
        return {
            "thought_process": result.thought_process,
            "answer": result.answer,
            "is_context_sufficient": result.is_context_sufficient,
            "sources": result.used_sources,
        }
