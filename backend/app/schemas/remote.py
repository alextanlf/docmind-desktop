from __future__ import annotations

from app.schemas.common import WireModel


class LoginStatus(WireModel):
    logged_in: bool
    account_label: str | None = None
    requires_login: bool


class LoginResult(LoginStatus):
    pass


class BrowserInstallResult(WireModel):
    installed: bool
    message: str


class RemoteRepository(WireModel):
    remote_id: str
    name: str
    url: str | None = None


class RemoteDocument(WireModel):
    remote_id: str
    repository_id: str
    title: str
    url: str | None = None


class RemoteDocumentContent(RemoteDocument):
    content: str


class CreateRemoteRepositoryRequest(WireModel):
    name: str


class CreateRemoteDocumentRequest(WireModel):
    repository_id: str
    title: str
    content: str


class UpdateRemoteDocumentRequest(WireModel):
    document_id: str
    title: str
    content: str


class ProviderCapabilitiesView(WireModel):
    browser_install: bool = False
    marker_lookup: bool = False


class ProviderSummaryView(WireModel):
    """A remotely usable knowledge-base provider exposed to the desktop app."""

    name: str
    label: str
    configured: bool
    capabilities: ProviderCapabilitiesView = ProviderCapabilitiesView()
