"""Generic remote-provider registry behaviour exposed over the HTTP API."""
from __future__ import annotations

from app.remote.provider import ProviderIdentity
from tests.conftest import install_remote_provider


class _NoBrowserProvider:
    """A registered provider that cannot install a login browser."""

    identity = ProviderIdentity(name="minimal", label="最小来源")

    async def close(self) -> None:
        return None


def test_provider_list_reports_configured_capabilities(client, auth_headers) -> None:
    response = client.get("/api/remote/providers", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "yuque",
            "label": "语雀",
            "configured": True,
            "capabilities": {"browserInstall": True, "markerLookup": True},
        }
    ]


def test_install_browser_rejects_a_provider_without_the_capability(client, auth_headers) -> None:
    install_remote_provider(client.app, _NoBrowserProvider())

    response = client.post(
        "/api/remote/providers/minimal/browser/install", headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "REMOTE_CAPABILITY_UNSUPPORTED"


def test_unknown_provider_is_reported(client, auth_headers) -> None:
    response = client.get("/api/remote/providers/feishu/status", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REMOTE_PROVIDER_UNKNOWN"


def test_capabilities_are_reflected_in_the_provider_summary(client, auth_headers) -> None:
    install_remote_provider(client.app, _NoBrowserProvider())

    response = client.get("/api/remote/providers", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()[0]["capabilities"] == {"browserInstall": False, "markerLookup": False}
