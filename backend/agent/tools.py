from typing import Callable

from langchain_core.tools import tool
from services.vector_service import VectorStoreService


MIN_PHOTO_SCORE = 0.3 # the accuracy of the related fotos 

def build_tools(vector_service: VectorStoreService) -> list[Callable]:
    """One tool - one responsibility"""

    @tool
    async def search_about_me(query: str) -> str:
        """Search the personal knowledge base (notes about Andriy: biography,
        skills, projects, hobbies, education, work experience).
        Use it for ANY factual question. Pass a short, focused search query; for broad
        topics call it several times with different sub-topics. Results include
        the original code snippets from the notes."""
        results = await vector_service.search(query, limit=8)
        if not results:
            return "Nothing found"
        return "\n\n---\n\n".join(f"[source: {r['metadata'].get('source')}]\n{r['text']}" for r in results)

    @tool
    async def find_photos(query: str) -> str:
        """Find photos of Andriy or related to a topic (e.g. 'travel', 'conference',
        'workplace'). Returns image URLs with captions. Call it when the user asks to
        show/see a photo, or when a picture would clearly help the answer."""
        results = await vector_service.search(query, limit=8, only_images=True)
        lines = [
            f"- {r['metadata']['image_url']} | {r['text'].splitlines()[0]}"
            for r in results 
            if r["score"] >= MIN_PHOTO_SCORE
        ]
        return "\n".join(lines) or "No photos found"
    
    return [search_about_me, find_photos]
