from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.schemas.remote import (
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    LoginResult,
    LoginStatus,
    RemoteDocument,
    RemoteDocumentContent,
    RemoteRepository,
    UpdateRemoteDocumentRequest,
)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Optional abilities a remote provider may or may not support.

    ``browser_install``: the provider authenticates through a locally driven
    browser and can install it on demand (Playwright Chromium).
    ``marker_lookup``: the provider supports ``find_document_by_marker`` as a
    write-compensation mechanism for idempotent mutations.
    ``parent_node_write``: new documents may target a provider-native parent
    container (Feishu: ``parent_node_token``).
    """

    browser_install: bool = False
    marker_lookup: bool = False
    parent_node_write: bool = False


@dataclass
class ProviderIdentity:
    """Stable metadata every provider must expose."""

    name: str
    label: str
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)


@runtime_checkable
class BrowserInstallCapable(Protocol):
    """Providers whose login flow depends on a locally installed browser."""

    async def install_browser(self) -> object: ...


class RemoteProvider(Protocol):
    """A pluggable remote knowledge-base (语雀 today, 飞书文档 and others later).

    The contract is deliberately transport agnostic: implementations may use an
    official open API, browser automation, or anything else. Consumers resolve
    providers through ``app.remote.registry.ProviderRegistry`` by name and must
    not depend on concrete implementation types.
    """

    identity: ProviderIdentity

    async def login_status(self) -> LoginStatus: ...

    async def begin_login(self) -> LoginResult: ...

    async def list_repositories(self) -> list[RemoteRepository]: ...

    async def create_repository(
        self, request: CreateRemoteRepositoryRequest
    ) -> RemoteRepository: ...

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]: ...

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument: ...

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None: ...

    async def document_exists(self, repository_id: str, document_id: str) -> bool: ...

    async def read_document(self, document_id: str) -> RemoteDocumentContent: ...

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument: ...

    async def delete_document(self, document_id: str, repository_id: str) -> None: ...

    async def close(self) -> None: ...
