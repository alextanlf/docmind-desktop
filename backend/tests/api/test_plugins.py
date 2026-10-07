"""Plugin catalogue: derivation from the registry, and third-party discovery.

Two things are locked here:

* the catalogue is *derived*, so registering a provider is the only step needed
  for its card to exist — there is no second list anywhere;
* the page is flat. Grouping by vendor or by purpose is exactly the behaviour
  that was removed, and a test asserting "no section headings" is the cheapest
  way to keep it from creeping back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.plugins import loader
from app.plugins.catalog import PluginCatalog
from app.plugins.loader import PluginContribution, load_plugins
from app.plugins.manifest import PluginManifest
from app.remote.credentials import (
    CredentialChannelSpec,
    CredentialStore,
    ProviderCredentialSpec,
)
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry
from app.remote.notifications import NotificationHub, NotificationTarget


class _DocsProvider:
    identity = ProviderIdentity(
        name="acme",
        label="Acme Docs",
        summary="把 Acme 文档导入 DocMind",
        icon="library",
        keywords=("acme", "wiki"),
        homepage="https://acme.test/docs",
        version="1.2.0",
        capabilities=ProviderCapabilities(browser_install=True),
    )

    async def close(self) -> None:
        return None


class _ChattyProvider:
    identity = ProviderIdentity(name="acme-chat", label="Acme Chat", icon="bell")

    async def close(self) -> None:
        return None


def _spec() -> ProviderCredentialSpec:
    return ProviderCredentialSpec(
        provider="acme",
        channels=(
            CredentialChannelSpec(
                name="web",
                label="Acme 网页登录",
                has_secret=False,
                summary="浏览器里登录 Acme",
                icon="login",
                keywords=("浏览器",),
            ),
            CredentialChannelSpec(
                name="api",
                label="Acme API",
                has_secret=True,
                default_secret_ref="acme:token",
                summary="用令牌读取 Acme",
                icon="key",
                keywords=("token",),
            ),
        ),
    )


def _catalog(*providers, store: CredentialStore) -> PluginCatalog:
    registry = ProviderRegistry(store)
    for provider in providers:
        registry.register(provider, always_configured=True, credential_spec=_spec())
    return PluginCatalog(registry, store)


# -- derivation -------------------------------------------------------------


def test_one_card_per_channel_not_per_provider(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    manifests = catalog.manifests()

    # The whole point of the redesign: 「Acme 网页登录」 and 「Acme API」 are two
    # things a user chooses between, so they are two cards.
    assert [m.id for m in manifests] == ["acme:web", "acme:api"]
    assert [m.label for m in manifests] == ["Acme 网页登录", "Acme API"]
    assert {m.provider_label for m in manifests} == {"Acme Docs"}


def test_card_carries_its_own_copy_and_search_terms(store: CredentialStore) -> None:
    manifest = _catalog(_DocsProvider(), store=store).manifests()[0]

    # Every string on the card is plugin-declared. A generic UI-side string
    # would either name vendors or go stale for a third-party plugin.
    assert manifest.summary == "浏览器里登录 Acme"
    assert manifest.icon == "login"
    assert manifest.provider_label == "Acme Docs"
    assert manifest.homepage == "https://acme.test/docs"
    assert manifest.version == "1.2.0"
    # Provider keywords are merged in so searching the integration name works
    # from any of its cards.
    assert set(manifest.keywords) >= {"浏览器", "acme", "wiki"}


def test_provider_without_credential_channels_still_gets_a_card(store: CredentialStore) -> None:
    registry = ProviderRegistry(store)
    registry.register(_ChattyProvider(), always_configured=True)
    catalog = PluginCatalog(registry, store)

    manifests = catalog.manifests()

    # Otherwise a plugin needing no stored secret would be invisible in settings
    # even though it works — and the user could not log into it from the UI.
    assert [m.id for m in manifests] == ["acme-chat:core"]
    assert manifests[0].has_secret is False
    assert manifests[0].icon == "bell"


def test_a_channel_may_override_the_providers_icon(store: CredentialStore) -> None:
    # A bot webhook is not a library even though it ships inside a docs plugin,
    # which is why the override exists.
    spec = ProviderCredentialSpec(
        provider="acme",
        channels=(
            CredentialChannelSpec(
                name="bot", label="Acme 机器人", has_secret=False, icon="bell"
            ),
        ),
    )
    registry = ProviderRegistry(store)
    registry.register(_DocsProvider(), always_configured=True, credential_spec=spec)

    manifest = PluginCatalog(registry, store).manifests()[0]

    assert manifest.icon == "bell"


def test_channel_without_icon_falls_back_to_the_provider(store: CredentialStore) -> None:
    spec = ProviderCredentialSpec(
        provider="acme",
        channels=(CredentialChannelSpec(name="plain", label="Acme", has_secret=False),),
    )
    registry = ProviderRegistry(store)
    registry.register(_DocsProvider(), always_configured=True, credential_spec=spec)

    assert PluginCatalog(registry, store).manifests()[0].icon == "library"


def test_card_reflects_live_credential_state(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)
    assert catalog.manifests()[1].state == "disconnected"

    store.save_secret("acme", "api", "t0ken", "acme:token")
    store.mark_state("acme", "api", "verified", account_label="alice")

    manifest = catalog.manifests()[1]
    assert manifest.configured is True
    assert manifest.state == "verified"
    assert manifest.account_label == "alice"


# -- search -----------------------------------------------------------------


def test_empty_query_returns_everything(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    assert catalog.search("") == catalog.manifests()
    assert catalog.search("   ") == catalog.manifests()


def test_search_matches_labels_and_declared_aliases(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    # "acme" appears in no Chinese label — it is only findable because the
    # provider name and its keywords are part of the searchable text.
    assert [m.id for m in catalog.search("acme")] == ["acme:web", "acme:api"]
    assert [m.id for m in catalog.search("wiki")] == ["acme:web", "acme:api"]
    # Per-channel aliases only match their own card.
    assert [m.id for m in catalog.search("token")] == ["acme:api"]
    assert [m.id for m in catalog.search("浏览器")] == ["acme:web"]


def test_search_is_case_insensitive_and_ands_the_terms(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    assert [m.id for m in catalog.search("ACME")] == ["acme:web", "acme:api"]
    # Two terms must both match, so this narrows rather than widens.
    assert [m.id for m in catalog.search("acme token")] == ["acme:api"]
    assert catalog.search("acme nonexistent") == []


def test_search_looks_at_the_summary_too(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    assert [m.id for m in catalog.search("令牌")] == ["acme:api"]


# -- API --------------------------------------------------------------------


def test_plugin_endpoint_lists_the_whole_catalogue(
    client: TestClient, auth_headers, store: CredentialStore
) -> None:
    client.app.state.plugin_catalog = _catalog(_DocsProvider(), store=store)

    response = client.get("/api/plugins", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [plugin["id"] for plugin in body] == ["acme:web", "acme:api"]
    # camelCase over the wire; the renderer never sees snake_case.
    assert body[0]["providerLabel"] == "Acme Docs"
    assert body[0]["browserInstall"] is True


def test_plugin_endpoint_filters_via_q(client: TestClient, auth_headers, store) -> None:
    client.app.state.plugin_catalog = _catalog(_DocsProvider(), store=store)

    response = client.get("/api/plugins", params={"q": "token"}, headers=auth_headers)

    assert [plugin["id"] for plugin in response.json()] == ["acme:api"]


def test_plugin_endpoint_requires_a_token(client: TestClient, store) -> None:
    client.app.state.plugin_catalog = _catalog(_DocsProvider(), store=store)

    assert client.get("/api/plugins").status_code == 401


# -- third-party discovery --------------------------------------------------


class _FakeEntryPoint:
    def __init__(self, name: str, produced, *, raises: bool = False) -> None:
        self.name = name
        self._produced = produced
        self._raises = raises

    def load(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._produced


def _notification_target(name: str) -> NotificationTarget:
    async def _send(event) -> None:  # pragma: no cover - never delivered here
        return None

    return NotificationTarget(name=name, send=_send, is_ready=lambda: False)


def test_third_party_plugin_registers_onto_the_shared_registry(
    store: CredentialStore, monkeypatch
) -> None:
    registry = ProviderRegistry(store)
    hub = NotificationHub()
    monkeypatch.setattr(
        loader,
        "_iter_entry_points",
        lambda: (
            _FakeEntryPoint(
                "acme",
                PluginContribution(
                    provider=_DocsProvider(),
                    credential_spec=_spec(),
                    notifications=(_notification_target("acme:bot"),),
                ),
            ),
        ),
    )

    diagnostics = load_plugins(registry, hub)

    assert diagnostics.failed == []
    assert registry.names() == ["acme"]
    # A plugin's notification target lands in the same hub as the built-in ones,
    # so business code never learns where it came from.
    assert "acme:bot" in set(hub.names())
    # And its card appears with no UI change at all.
    assert [m.id for m in PluginCatalog(registry, store).manifests()] == ["acme:web", "acme:api"]


def test_a_broken_plugin_is_reported_and_skipped(store: CredentialStore, monkeypatch) -> None:
    registry = ProviderRegistry(store)
    monkeypatch.setattr(
        loader,
        "_iter_entry_points",
        lambda: (
            _FakeEntryPoint("broken", None, raises=True),
            _FakeEntryPoint("wrong-shape", ["not a contribution"]),
            _FakeEntryPoint(
                "ok",
                PluginContribution(provider=_DocsProvider(), credential_spec=_spec()),
            ),
        ),
    )

    diagnostics = load_plugins(registry)

    # A missing integration is a degraded feature; refusing to boot is not an
    # acceptable response to one. The good plugin still loads.
    assert [item["name"] for item in diagnostics.failed] == ["broken", "wrong-shape"]
    assert registry.names() == ["acme"]
    assert PluginCatalog(registry, store).manifests()


def test_diagnostics_endpoint_exposes_failures(client: TestClient, auth_headers) -> None:
    class _Diag:
        def as_dicts(self):
            return [{"name": "broken", "error": "boom"}]

    client.app.state.plugin_diagnostics = _Diag()

    response = client.get("/api/plugins/diagnostics", headers=auth_headers)

    assert response.status_code == 200, response.text
    # Surfaced, not logged-and-forgotten: a plugin that silently failed looks
    # identical to one that was never installed.
    assert response.json() == [{"name": "broken", "error": "boom"}]


def test_manifest_view_is_the_wire_contract(store: CredentialStore) -> None:
    manifest = _catalog(_DocsProvider(), store=store).manifests()[0]

    view = manifest.view()

    assert view.id == "acme:web"
    assert view.provider_label == "Acme Docs"
    assert view.keywords


@pytest.mark.parametrize("returned", [0, None])
def test_no_plugins_yields_an_empty_catalogue(store: CredentialStore, returned) -> None:
    registry = ProviderRegistry(store)
    registry.register(_ChattyProvider(), always_configured=bool(returned))

    assert len(PluginCatalog(registry, store).manifests()) == 1
    assert PluginManifest is not None

# -- extensibility: the contract a new plugin author relies on --------------


def test_a_vendor_absent_from_this_codebase_gets_a_full_card(store: CredentialStore) -> None:
    """The one promise the settings page makes to a plugin author.

    A provider invented here — named nowhere in this repository, registered the
    only way a third party can — must produce a complete, searchable card. If
    this ever needs a matching edit in ``catalog.py`` or the renderer, the
    extensibility claim is false and this test is the thing that noticed.
    """

    class _Zenith:
        identity = ProviderIdentity(
            name="zenith",
            label="Zenith Board",
            summary="把 Zenith 工单导入 DocMind",
            icon="key",
            keywords=("zenith", "工单", "ticket"),
            homepage="https://zenith.test",
            version="9.9.9",
        )

        async def close(self) -> None:
            return None

    registry = ProviderRegistry(store)
    registry.register(
        _Zenith(),
        always_configured=True,
        credential_spec=ProviderCredentialSpec(
            provider="zenith",
            channels=(
                CredentialChannelSpec(
                    name="api",
                    label="Zenith API",
                    has_secret=True,
                    default_secret_ref="zenith:token",
                    summary="用令牌读取 Zenith",
                ),
            ),
        ),
    )

    manifests = PluginCatalog(registry, store).manifests()

    # Every string the card renders is plugin-declared, so the renderer needs no
    # per-vendor knowledge to draw it.
    assert [m.id for m in manifests] == ["zenith:api"]
    assert manifests[0].label == "Zenith API"
    assert manifests[0].provider_label == "Zenith Board"
    assert manifests[0].summary == "用令牌读取 Zenith"
    assert manifests[0].icon == "key"
    assert manifests[0].version == "9.9.9"
    # And it is findable by a term that appears in no label.
    assert [m.id for m in PluginCatalog(registry, store).search("工单")] == ["zenith:api"]


def test_keywords_are_merged_without_duplicates(store: CredentialStore) -> None:
    """A provider whose own keywords repeat its name must not ship it twice.

    The provider name is prepended to every manifest, and repeating it in
    ``keywords`` is the most natural thing an author can write — so this is the
    default shape, not an exotic one. Duplicates are invisible on the card but
    inflate the search haystack, where they cost a redundant substring check per
    keystroke.
    """
    registry = ProviderRegistry(store)
    # Dirty input on purpose: the name appears in the provider keywords AND in
    # the channel keywords AND is prepended by the catalogue.
    registry.register(
        _DocsProvider(),
        always_configured=True,
        credential_spec=ProviderCredentialSpec(
            provider="acme",
            channels=(
                CredentialChannelSpec(
                    name="api",
                    label="Acme API",
                    has_secret=True,
                    default_secret_ref="acme:token",
                    keywords=("acme", "ACME", "token"),
                ),
            ),
        ),
    )

    keywords = PluginCatalog(registry, store).manifests()[0].keywords

    # Order preserved and first spelling kept: the channel's own aliases lead,
    # the provider-wide ones follow, and the name — prepended by the catalogue —
    # collapses into the channel's own mention of it. Case-insensitive, so a
    # shouty "ACME" cannot sneak past as a second term.
    assert keywords == ("acme", "token", "wiki")
    # Every term still searchable — merging must not drop anything.
    assert [m.id for m in PluginCatalog(registry, store).search("wiki")] == ["acme:api"]
    assert [m.id for m in PluginCatalog(registry, store).search("ACME")] == ["acme:api"]


def test_merging_keywords_tolerates_missing_and_empty_groups() -> None:
    from app.plugins.catalog import _merge_keywords

    assert _merge_keywords((), ()) == ()
    assert _merge_keywords(("a",), ()) == ("a",)
    assert _merge_keywords((), ("b",)) == ("b",)
    assert _merge_keywords(None, ("c",)) == ("c",)
    assert _merge_keywords(("a", "a"), ("A", "b")) == ("a", "b")
