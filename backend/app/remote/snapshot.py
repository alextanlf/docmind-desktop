from __future__ import annotations

import hashlib

from app.remote.provider import RemoteProvider
from app.schemas.sync import RemoteDocumentState


async def read_remote_snapshot(
    provider: RemoteProvider, repository_id: str
) -> list[tuple[RemoteDocumentState, str]]:
    """Read the current (state, content) snapshot of a remote repository."""
    states: list[tuple[RemoteDocumentState, str]] = []
    for document in await provider.list_documents(repository_id):
        content = await provider.read_document(document.remote_id)
        digest = hashlib.sha256(content.content.encode("utf-8")).hexdigest()
        states.append(
            (
                RemoteDocumentState(
                    document_id=document.remote_id,
                    title=document.title,
                    content_sha256=digest,
                    url=document.url or document.remote_id,
                ),
                content.content,
            )
        )
    return states
