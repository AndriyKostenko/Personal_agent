import sys, statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tiktoken
from pipeline.chunker import MarkdownChunker

enc = tiktoken.get_encoding("cl100k_base")
sizes = []
for p in Path(sys.argv[1]).rglob("*.md"):
    for c in MarkdownChunker().split(p.read_text(encoding="utf-8"), p.stem):
        sizes.append(len(enc.encode(c.text)))
print(
    f"chunks={len(sizes)} median={statistics.median(sizes)} max={max(sizes)} "
    f"<60 tokens={sum(s < 60 for s in sizes)}"
)
