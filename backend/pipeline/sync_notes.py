import hashlib
import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import json
import asyncio
from httpx import AsyncClient, HTTPError
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.settings import Settings
import shutil
from urllib.parse import unquote
from pipeline.image_captioner import PhotoCaptioner

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMG_RE = re.compile(
    r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|!\[[^\]]*\]\(([^)\s]+)\)"
)  # модульная константа


class FileProcessingManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.knowledge_path = Path(self.settings.OBSIDIAN_KNOWLEDGE_BASE_PATH)

        # setup the state file for tracking sync progress
        self.state_file = Path("sync_state.json")
        self.sync_state = self._load_state()

        self.api_url = getattr(
            self.settings, "INDEX_API_URL", "http://127.0.0.1:8000/api/v1/index"
        )

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=150, separators=["\n\n", "\n", " ", ""]
        )
        self.captioner = PhotoCaptioner(settings)
        self.media_dir = Path(settings.MEDIA_DIR)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.image_index = {
            p.name: p
            for p in self.knowledge_path.rglob("*")
            if p.suffix.lower() in IMG_EXTS
        }

        self._ensure_knowledge_path()

    def _ensure_knowledge_path(self):
        """Checking the directory."""
        if not self.knowledge_path.exists():
            print(f"Knowledge path does not exist: {self.knowledge_path}. Creating...")
            return

    def _load_state(self):
        """Loading the existing state from the state file."""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {}

    def _save_state(self):
        """Saving the current state (hash) to the state file."""
        with open(self.state_file, "w") as f:
            json.dump(self.sync_state, f)

    @staticmethod
    def _get_file_hash(content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()

    async def process_file(self, file_path: Path, client: AsyncClient):
        """Parsing one Markdown file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading file: {file_path}: {e}")
            return

        # Deduplication logic
        file_key = str(file_path.relative_to(self.knowledge_path))
        current_hash = FileProcessingManager._get_file_hash(content)
        if self.sync_state.get(file_key) == current_hash:
            print(f"File {file_key} is already synced, skipping")
            return

        metadata = {}
        text_content = content

        # 1. Extracting the YAML front matter
        if content.startswith("---\n"):
            yaml_end = content.find("---\n", 4)
            if yaml_end != -1:
                yaml_content = content[4:yaml_end]
                try:
                    parsed_yaml = yaml.safe_load(yaml_content)
                    if isinstance(parsed_yaml, dict):
                        metadata = parsed_yaml
                except yaml.YAMLError as e:
                    print(f"YAML parsing error in {file_path.name}: {e}")

                text_content = content[yaml_end + 4 :].strip()

        # 2. Captioning the linked images, then removing the links from the text chunks
        images_ok = await self.process_images(
            text_content, file_path.name, metadata, client
        )
        text_content = IMG_RE.sub("", text_content)

        # 3. Splitting the text
        chunks = self.text_splitter.split_text(text_content)

        # 4. Sending to API
        success = True
        for index, chunk in enumerate(chunks):
            payload = {
                "text": chunk,
                "source": file_path.name,
                "metadata": {**metadata, "chunk_index": index},
            }
            try:
                response = await client.post(url=self.api_url, json=payload)
                response.raise_for_status()
                print(
                    f"File {file_path.name} | Chunk {index + 1}/{len(chunks)} sent successfully"
                )
            except HTTPError as e:
                print(f"Error sending chunk {index} for {file_path.name}: {e}")
                success = False

        if success and images_ok:
            self.sync_state[file_key] = current_hash
            self._save_state()

    def _publish_image(self, src: Path, digest: str) -> str:
        dst = self.media_dir / f"{digest[:16]}{src.suffix.lower()}"
        if not dst.exists():
            shutil.copy2(src, dst)
        return f"{self.settings.MEDIA_BASE_URL}/{dst.name}"

    async def index_image(
        self,
        src: Path,
        note_name: str,
        context: str,
        base_meta: dict,
        client: AsyncClient,
        prefix: str = "",
    ) -> bool:
        """Captions one image with the vision model and indexes the caption as a document."""
        # not paying for vision/embeddings again if the file did not change
        file_hash = hashlib.md5(src.read_bytes()).hexdigest()
        state_key = f"img:{note_name}"
        if self.sync_state.get(state_key) == file_hash:
            return True

        cap, digest = await self.captioner.caption(src, context)
        url = self._publish_image(src, digest)

        doc_text = f"{prefix}{cap.caption}\nTags: {', '.join(cap.tags)}"
        if cap.visible_text:
            doc_text += f"\nText on image: {cap.visible_text}"

        payload = {
            "text": doc_text,
            "source": note_name,
            "metadata": {
                **base_meta,
                "type": "image",
                "image_url": url,
                "chunk_index": f"img-{digest[:12]}",
            },  # -> deterministic point id, no duplicates
        }
        try:
            (await client.post(self.api_url, json=payload)).raise_for_status()
        except HTTPError as e:
            print(f"Error indexing image {src.name}: {e}")
            return False

        self.sync_state[state_key] = file_hash
        self._save_state()
        print(f"Image {src.name} indexed")
        return True

    async def process_images(
        self, text: str, note_name: str, base_meta: dict, client: AsyncClient
    ) -> bool:
        """Indexes the images that are linked from a note."""
        ok = True
        for m in IMG_RE.finditer(text):
            ref = unquote(m.group(1) or m.group(2))
            if ref.startswith("http"):
                continue
            src = self.image_index.get(Path(ref).name)
            if src is None:
                print(f"Image not found: {ref} (in {note_name})")
                continue

            context = text[max(0, m.start() - 300) : m.end() + 300]
            ok &= await self.index_image(
                src, f"{note_name}#{src.name}", context, base_meta, client
            )
        return ok

    async def process_photo_folders(self, client: AsyncClient) -> None:
        """Indexes every photo from PHOTO_FOLDERS, even if no note links to it."""
        for folder in self.settings.PHOTO_FOLDERS:
            root = self.knowledge_path / folder
            if not root.is_dir():
                print(f"Photo folder not found: {root}")
                continue

            # context = the notes that lie next to the photos
            context = "\n".join(
                p.read_text(encoding="utf-8") for p in root.glob("*.md")
            )[:1500]
            photos = sorted(p for p in root.iterdir() if p.suffix.lower() in IMG_EXTS)
            for img in photos:
                await self.index_image(
                    img,
                    f"{folder}/{img.name}",
                    context,
                    {},
                    client,
                    prefix="Photo of Andriy (the author of these notes). ",
                )

    async def run(self):
        """Collecting all files and starting the async processing."""
        md_files = list(self.knowledge_path.rglob("*.md"))

        if not md_files:
            print(f"No markdown files found in {self.knowledge_path}")
            return

        print(f"Found {len(md_files)} markdown files. Starting sync...")

        # creating a semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(5)

        async def process_file_with_semaphore(file_path, client):
            async with semaphore:
                await self.process_file(file_path, client)

        # using one client only (Connection Pooling)
        headers = {"X-Admin-Key": self.settings.ADMIN_API_KEY} if self.settings.ADMIN_API_KEY else {}
        async with AsyncClient(timeout=30.0, headers=headers) as client:
            tasks = [
                process_file_with_semaphore(file_path, client) for file_path in md_files
            ]
            await asyncio.gather(*tasks)
            await self.process_photo_folders(client)


if __name__ == "__main__":
    settings = Settings()
    manager = FileProcessingManager(settings)
    asyncio.run(manager.run())
