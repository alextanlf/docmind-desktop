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
    # Provider-native hierarchy id (Feishu: wiki node token). Providers that
    # do not support nested writes ignore it.
    parent_id: str | None = None


class UpdateRemoteDocumentRequest(WireModel):
    document_id: str
    title: str
    content: str


class ProviderCapabilitiesView(WireModel):
    browser_install: bool = False
    marker_lookup: bool = False
    parent_node_write: bool = False
    # The error code this provider raises when its login browser is missing,
    # so the client can offer the install action without naming a vendor.
    browser_unavailable_code: str | None = None


class ProviderSummaryView(WireModel):
    """A remotely usable knowledge-base provider exposed to the desktop app."""

    name: str
    label: str
    configured: bool
    capabilities: ProviderCapabilitiesView = ProviderCapabilitiesView()


class CredentialChannelView(WireModel):
    """Secret-free view of one provider credential channel."""

    provider: str
    channel: str
    label: str
    configured: bool
    state: str  # 'verified' | 'unverified' | 'disconnected'
    account_label: str | None = None
    has_secret: bool = False
    # Channel-declared presentation hints. The generic form uses them instead
    # of inventing provider-specific copy, so a URL-shaped credential can show
    # an example and an out-of-band creation link.
    secret_placeholder: str | None = None
    help_url: str | None = None
    help_label: str | None = None


class SaveCredentialRequest(WireModel):
    secret: str


class CredentialTestResult(WireModel):
    connected: bool
    message: str
    label: str | None = None
