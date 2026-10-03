import base64
import hashlib
import io
import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from PIL import Image
from pydantic import BaseModel, Field

from core.settings import Settings

SYSTEM = (
    "You write search-friendly captions for photos from a personal archive.\n"
    "- Describe only what is clearly visible: scene, setting, activity, objects.\n"
    "- Do NOT identify anyone by facial features. Say 'a person' / 'a group of people'.\n"
    "- Use NOTE CONTEXT (the author's text around the photo) to name the event, place "
    "or people ONLY if the context states it explicitly. Never guess.\n"
    "- Write the caption in the same language as the NOTE CONTEXT."
)


class PhotoCaption(BaseModel):
    caption: str = Field(description="1-2 factual sentences describing the photo.")
    tags: list[str] = Field(description="3-8 short lowercase keywords: place type, activity, objects.")
    visible_text: str = Field(default="", description="Legible text on the image (signs, slides). Empty if none.")


class PhotoCaptioner:
    MAX_SIDE = 1024

    def __init__(self, settings: Settings, cache_path: Path = Path("caption_cache.json")):
        llm = ChatOpenAI(
            model=settings.VISION_MODEL,
            api_key=settings.OPEN_ROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
        )
        self.chain = llm.with_structured_output(PhotoCaption)
        self.cache_path = cache_path
        self.cache: dict = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    def _prepare(self, path: Path) -> tuple[str, str]:
        """Downscale to JPEG -> (sha256 of pixels, base64). Fewer tokens = cheaper."""
        img = Image.open(path)
        img.thumbnail((self.MAX_SIDE, self.MAX_SIDE))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        data = buf.getvalue()
        return hashlib.sha256(data).hexdigest(), base64.b64encode(data).decode()

    async def caption(self, path: Path, note_context: str) -> tuple[PhotoCaption, str]:
        digest, b64 = self._prepare(path)
        if digest in self.cache:                       # already captioned -> free
            return PhotoCaption(**self.cache[digest]), digest

        message = HumanMessage(content=[
            {"type": "text", "text": f"NOTE CONTEXT:\n{note_context}\n\nCaption this photo."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ])
        result: PhotoCaption = await self.chain.ainvoke([SystemMessage(content=SYSTEM), message])

        self.cache[digest] = result.model_dump()
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2))
        return result, digest
