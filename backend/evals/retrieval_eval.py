import asyncio, json, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.settings import settings
from services.vector_service import VectorStoreService

K = 8  # the same k as in search_about_me


def norm(source: str) -> str:
    return Path(
        source
    ).name  # v1 stored only the file name, v2 the relative path: compare like with like


async def main() -> None:
    lines = (
        Path(__file__)
        .with_name("dataset.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    rows = [
        r
        for r in map(json.loads, filter(str.strip, lines))
        if r.get("expected_sources")
    ]
    vs = VectorStoreService(settings.COLLECTION_NAME, settings)

    hit1, hitk, rr, recall, precision = [], [], [], [], []
    for r in rows:
        got = [
            norm(x["metadata"].get("source", ""))
            for x in await vs.search(r["question"], limit=K)
        ]
        expected = {norm(s) for s in r["expected_sources"]}
        rank = next((i + 1 for i, s in enumerate(got) if s in expected), None)
        hit1.append(rank == 1)
        hitk.append(rank is not None)
        rr.append(1 / rank if rank else 0.0)  # MRR: how high the first right note is
        recall.append(
            len(expected & set(got)) / len(expected)
        )  # how many of the right notes were found
        precision.append(
            sum(s in expected for s in got) / len(got) if got else 0.0
        )  # how much of top-k is noise
        if rank is None:
            print(f"MISS {r['id']}: {r['question'][:60]!r} -> {got[:3]}")

    print(
        f"{settings.COLLECTION_NAME}: n={len(rows)} hit@1={statistics.mean(hit1):.2f} "
        f"hit@{K}={statistics.mean(hitk):.2f} MRR={statistics.mean(rr):.2f} "
        f"recall@{K}={statistics.mean(recall):.2f} precision@{K}={statistics.mean(precision):.2f}"
    )


asyncio.run(main())
