from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


class LocalKnowledgeStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "knowledge"

    def save(self, identifier: str, title: str, content: str) -> tuple[str, str]:
        self.root.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title).strip("-")[:80] or "knowledge"
        target = self.root / f"{safe}-{identifier}.md"
        partial = target.with_suffix(".md.partial")
        payload = content.encode("utf-8")
        with partial.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, target)
        directory_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return str(target.relative_to(self.root.parent)), hashlib.sha256(payload).hexdigest()
