import asyncio, json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from qdrant_client import models

from core.settings import settings
from services.vector_service import VectorStoreService


class QA(BaseModel):
    question: str = Field(
        description="A natural question a recruiter or developer could ask that this fragment answers. Paraphrase, do not copy phrases: never mention 'the note' or 'the text'."
    )
    must_include: list[str] = Field(
        description="1-3 LANGUAGE-NEUTRAL key terms a correct answer must contain: identifiers, numbers, "
        "English technical terms. The assistant always answers in English, so never use "
        "Russian or Ukrainian words and never use phrases longer than 3 words."
    )


async def main(n: int = 15) -> None:
    vs = VectorStoreService(settings.COLLECTION_NAME, settings)
    not_image = models.Filter(
        must_not=[
            models.FieldCondition(
                key="metadata.type", match=models.MatchValue(value="image")
            )
        ]
    )
    points, _ = vs.qdrant_client.scroll(
        vs.collection_name, scroll_filter=not_image, limit=2000, with_payload=True
    )
    random.seed(42)
    llm = ChatOpenAI(
        model=settings.VALIDATION_MODEL,
        api_key=settings.OPEN_ROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
    ).with_structured_output(QA)
    out = Path(__file__).with_name("dataset.candidates.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for i, p in enumerate(random.sample(points, min(n, len(points)))):
            qa = await llm.ainvoke(
                f"Fragment from Andriy's notes:\n\n{p.payload['page_content']}"
            )
            f.write(
                json.dumps(
                    {
                        "id": f"gen-{i}",
                        "type": "fact",
                        "question": qa.question,
                        "expected_sources": [p.payload["metadata"]["source"]],
                        "must_include": qa.must_include,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"{out}: review it and copy the good rows into dataset.jsonl")


asyncio.run(main())
