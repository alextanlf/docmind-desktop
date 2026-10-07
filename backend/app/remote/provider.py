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
    browser and can prepare the matching WebDriver on demand.
    ``marker_lookup``: the provider supports ``find_document_by_marker`` as a
    write-compensation mechanism for idempotent mutations.
    ``parent_node_write``: new documents may target a provider-native parent
    container (Feishu: ``parent_node_token``).
    ``browser_unavailable_code``: the error code this provider raises when its
    login browser is missing. Declared rather than hard-coded in the UI so a
    client can offer the install action without naming a vendor — a generic
    "remote source" would be nonsense to show next to a Feishu login button.
    """

    browser_install: bool = False
    marker_lookup: bool = False
    parent_node_write: bool = False
    browser_unavailable_code: str | None = None


@dataclass
class ProviderIdentity:
    """Stable metadata every provider must expose."""

    name: str
    label: str
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    # One line saying what this integration is for. It is shown on the plugin
    # card and searched over, so it has to be provider-supplied: a generic
    # string would either name vendors in the UI layer or go stale the moment a
    # third-party plugin is installed.
    summary: str | None = None
    # Icon key the client maps to a glyph ("library", "bell", ...). A key rather
    # than a component or an icon font name, because plugins ship as data and
    # the renderer owns the glyph table.
    icon: str | None = None
    # Extra search terms beyond label/summary, for the names users actually type
    # ("wiki", " Lark", "飞书文档"). Case-insensitive on both ends.
    keywords: tuple[str, ...] = ()
    # The category tag's display text ("知识库"), declared rather than derived
    # from `purpose` by the client. Deriving it in the renderer meant the page
    # had to know what the purposes were called, which is copy the plugin owns.
    # Only used by a provider that declares no credential channels, since a
    # channel carries its own.
    tag: str | None = None
    # Where to read more about the integration. Opaque to the app; the client
    # only opens it externally.
    homepage: str | None = None
    # Free-form version string for the plugin itself, surfaced in the UI so a
    # user can tell two builds of the same provider apart when reporting bugs.
    version: str | None = None


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
