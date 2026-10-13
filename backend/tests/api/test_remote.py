"""Generic remote-provider registry behaviour exposed over the HTTP API."""
from __future__ import annotations

from dataclasses import fields

from fastapi.testclient import TestClient

from app.remote.provider import ProviderCapabilities, ProviderIdentity
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
                "documentPaths": True,
                "repositoryRename": True,
                "documentRename": True,
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
        "documentPaths": False,
        "repositoryRename": False,
        "documentRename": False,
        "browserUnavailableCode": None,
    }


def test_every_declared_capability_reaches_the_client(client, auth_headers) -> None:
    """``ProviderCapabilities`` 里每一个字段都必须出现在摘要里，一个都不能少。

    这条测试是为一次真实事故写的：``document_paths`` 加进 dataclass 之后忘了往
    ``ProviderCapabilitiesView`` 里映射，于是「这个来源报不报目录路径」这件事在
    传输层被静默丢掉 —— 前端拿到的永远是 False，而两边各自的单测都还是绿的。

    所以这里不列举字段，而是把 dataclass 的字段名逐个翻成 camelCase 去对：
    以后再加能力，忘了映射就会红在这一行。
    """

    class _EveryCapabilityProvider:
        identity = ProviderIdentity(
            name="everything",
            label="全能力",
            capabilities=ProviderCapabilities(
                browser_install=True,
                marker_lookup=True,
                parent_node_write=True,
                document_paths=True,
                repository_rename=True,
                document_rename=True,
                browser_unavailable_code="SOME_CODE",
            ),
        )

        async def close(self) -> None:
            return None

    install_remote_provider(client.app, _EveryCapabilityProvider())

    reported = client.get("/api/remote/providers", headers=auth_headers).json()[0]["capabilities"]

    def camel(name: str) -> str:
        head, *tail = name.split("_")
        return head + "".join(part.title() for part in tail)

    missing = {camel(field.name) for field in fields(ProviderCapabilities)} - set(reported)
    assert missing == set()
