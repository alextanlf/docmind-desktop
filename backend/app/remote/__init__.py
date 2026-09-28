from __future__ import annotations

from app.remote.credentials import (
    CredentialChannelSpec,
    CredentialChannelState,
    CredentialStore,
    ProviderCredentialSpec,
)
from app.remote.discovery import RemoteDiscovery
from app.remote.fake import FakeRemoteProvider
from app.remote.markers import extract_mutation_marker, strip_mutation_marker
from app.remote.provider import (
    BrowserInstallCapable,
    ProviderCapabilities,
    ProviderIdentity,
    RemoteProvider,
)
from app.remote.registry import ProviderRegistry
from app.remote.snapshot import read_remote_snapshot

__all__ = [
    "BrowserInstallCapable",
    "CredentialChannelSpec",
    "CredentialChannelState",
    "CredentialStore",
    "FakeRemoteProvider",
    "ProviderCapabilities",
    "ProviderCredentialSpec",
    "ProviderIdentity",
    "ProviderRegistry",
    "RemoteDiscovery",
    "RemoteProvider",
    "extract_mutation_marker",
    "read_remote_snapshot",
    "strip_mutation_marker",
]
