from __future__ import annotations

import re

# The mutation flow appends an HTML comment marker to written content so that
# a lost create/update acknowledgement can be compensated by scanning the
# remote repository for the marker (``find_document_by_marker``).

_MUTATION_MARKER_RE = re.compile(r"\n\n<!--\s*docmind-mutation:[^>]+-->\Z")
_MUTATION_MARKER_IDENT_RE = re.compile(r"<!--\s*(docmind-mutation:[A-Za-z0-9-]+)\s*-->")


def strip_mutation_marker(content: str) -> str:
    """Remove the trailing idempotency marker from written content."""
    return _MUTATION_MARKER_RE.sub("", content)


def extract_mutation_marker(content: str) -> str | None:
    """Return the full mutation marker embedded in ``content`` if any."""
    match = _MUTATION_MARKER_IDENT_RE.search(content)
    return match.group(1) if match else None
