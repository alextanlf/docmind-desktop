from __future__ import annotations

from difflib import SequenceMatcher

from app.schemas.versioning import DiffLine


def diff_versions(base: str, target: str) -> list[DiffLine]:
    matcher = SequenceMatcher(None, base.splitlines(), target.splitlines(), autojunk=False)
    lines: list[DiffLine] = []
    for tag, base_start, base_end, target_start, target_end in matcher.get_opcodes():
        if tag == "equal":
            lines.extend(
                DiffLine("context", line) for line in base.splitlines()[base_start:base_end]
            )
        elif tag == "delete":
            lines.extend(
                DiffLine("removed", line) for line in base.splitlines()[base_start:base_end]
            )
        elif tag == "insert":
            lines.extend(
                DiffLine("added", line)
                for line in target.splitlines()[target_start:target_end]
            )
        elif tag == "replace":
            lines.extend(
                DiffLine("removed", line) for line in base.splitlines()[base_start:base_end]
            )
            lines.extend(
                DiffLine("added", line)
                for line in target.splitlines()[target_start:target_end]
            )
    return lines
