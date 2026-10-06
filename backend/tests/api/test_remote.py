"""Generic remote-provider registry behaviour exposed over the HTTP API."""
from __future__ import annotations

from fastapi.testclient import TestClient

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
            "capabilities": {
                "browserInstall": True,
                "markerLookup": True,
                "parentNodeWrite": False,
                "browserUnavailableCode": None,
            },
        }
    ]


def test_browser_unavailable_code_travels_with_the_capability(
    production_client: TestClient, auth_headers
) -> None:
    """The client must learn the provider's own missing-browser code.

    It used to hardcode a vendor string, which was simply wrong for every
    other provider; the value now comes from the provider's declaration.
    """
    providers = production_client.get(
        "/api/remote/providers", headers=auth_headers
    ).json()
    by_name = {provider["name"]: provider for provider in providers}

    assert (
        by_name["yuque"]["capabilities"]["browserUnavailableCode"]
        == "YUQUE_BROWSER_UNAVAILABLE"
    )
    # Feishu drives its login through OAuth, not a local browser, so it must
    # not advertise a browser-install path at all.
    assert by_name["feishu"]["capabilities"]["browserInstall"] is False
    assert by_name["feishu"]["capabilities"]["browserUnavailableCode"] is None


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
    assert response.json()[0]["capabilities"] == {
        "browserInstall": False,
        "markerLookup": False,
        "parentNodeWrite": False,
        "browserUnavailableCode": None,
    }
