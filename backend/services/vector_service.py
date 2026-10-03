import asyncio
import uuid
from typing import Any

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from qdrant_client.models import VectorParams, Distance

from core.settings import Settings


class VectorStoreService:
    # fixed namespace: the same (source, chunk_index) always maps to the same point id
    CHUNK_ID_NAMESPACE = uuid.UUID("6f1c2f0e-6b0a-4c55-9d0c-2a4a1f0b7e11")

    def __init__(self, collection_name: str, settings: Settings):
        # one writer at a time: see add_documents()
        self._write_lock = asyncio.Lock()
        self.collection_name = collection_name
        self.settings = settings
        self.qdrant_client = QdrantClient(path=self.settings.QDRANT_STORAGE_PATH)
        # check_embedding_ctx_length=False: OpenRouter expects raw strings, not tiktoken token ids
        self.embeddings = OpenAIEmbeddings(
            model="openai/text-embedding-3-small",
            api_key=self.settings.OPEN_ROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            check_embedding_ctx_length=False,
        )
        self._ensure_the_collection()
        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=self.collection_name,
            embedding=self.embeddings,
        )

    def _ensure_the_collection(self):
        """Creating the collection if it does not exist"""
        if not self.qdrant_client.collection_exists(self.collection_name):
            self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            )

    @classmethod
    def _point_id(cls, doc: dict[str, Any]) -> str:
        """Deterministic id for chunks from the sync pipeline, so re-indexing overwrites instead of duplicating"""
        metadata = doc.get("metadata", {})
        if "source" in metadata and "chunk_index" in metadata:
            return str(
                uuid.uuid5(
                    cls.CHUNK_ID_NAMESPACE,
                    f"{metadata['source']}:{metadata['chunk_index']}",
                )
            )
        return str(uuid.uuid4())

    async def add_documents(self, documents: list[dict[str, Any]]):
        """Adds documents to the collection"""
        # The embedded Qdrant keeps its data in SQLite behind ONE connection, while
        # aadd_documents() runs in a thread pool. Parallel writes (the sync job sends several
        # requests at once) corrupt each other's transactions: "cannot commit - no transaction
        # is active" -> HTTP 500. Writes go one by one; searches are not blocked.
        async with self._write_lock:
            await self.vector_store.aadd_documents(
                documents=[
                    Document(
                        page_content=document["text"],
                        metadata=document.get("metadata", {}),
                    )
                    for document in documents
                ],
                ids=[self._point_id(document) for document in documents],
            )

    async def search(self, query: str, limit: int = 5, only_images: bool = False):
        is_image = models.FieldCondition(
            key="metadata.type", match=models.MatchValue(value="image")
        )
        flt = (
            models.Filter(must=[is_image])
            if only_images
            else models.Filter(must_not=[is_image])
        )
        results = await self.vector_store.asimilarity_search_with_score(
            query, k=limit, filter=flt
        )
        return [
            {"score": s, "text": d.page_content, "metadata": d.metadata}
            for d, s in results
        ]
