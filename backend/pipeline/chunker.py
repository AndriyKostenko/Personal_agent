import re
from dataclasses import dataclass, field
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter


HEADING_RE = re.compile(r"^ {0,3}(#{1,3})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
MIN_CHUNK_CHARS = 40


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)


class MarkdownChunker:
    """Structure-aware chunking: sectons by headinghs -> token-size split -> contextual header."""

    def __init__(self, chunk_tokens: int = 500, overlap_tokens: int = 75) -> None:
        self.size_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base",
            chunk_size=chunk_tokens,
            chunk_overlap=overlap_tokens,  # 15 %
            separators=RecursiveCharacterTextSplitter.get_separators_for_language(
                Language.MARKDOWN
            ),
        )

    @staticmethod
    def _sections(text: str) -> list[tuple[list[str], str]]:
        """(heading path, section text). Lines are kept verbatim; '#' inside ``` is not a heading."""
        sections, path, buf, in_code = [], [], [], False

        def flush():
            body = "\n".join(buf).strip("\n")
            if body.strip():
                sections.append((list(path), body))
            buf.clear()

        for line in text.splitlines():
            if FENCE_RE.match(line):
                in_code = not in_code
            m = None if in_code else HEADING_RE.match(line)
            if m:
                flush()
                level = len(m.group(1))
                path[:] = path[: level - 1] + [m.group(2)]
            else:
                buf.append(line)
        flush()
        return sections

    def split(self, text: str, title: str) -> list[Chunk]:
        links = sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(text)})
        text = WIKILINK_RE.sub(
            lambda m: m.group(2) or m.group(1), text
        )  # [[Note|alias]] -> alias

        chunks: list[Chunk] = []
        for path, body in self._sections(text):
            section = " > ".join(path)
            header = f"Note: {title}" + (f"\nSection: {section}" if section else "")
            for piece in self.size_splitter.split_text(body):
                if len(piece.strip()) < MIN_CHUNK_CHARS:
                    continue  # fragments without meaning only pollute top-k
                chunks.append(
                    Chunk(
                        text=f"{header}\n\n{piece}",
                        metadata={"title": title, "section": section, "links": links},
                    )
                )
        for i, c in enumerate(chunks):
            c.metadata["chunk_index"] = i
        return chunks
