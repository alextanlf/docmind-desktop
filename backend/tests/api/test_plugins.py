"""Plugin catalogue: derivation from contributions, and third-party discovery.

What is locked here:

* the catalogue is *derived*, so installing a contribution is the only step
  needed for its card to exist — there is no second list anywhere;
* the page is flat. Grouping by vendor or by purpose is exactly the behaviour
  that was removed, and a test asserting "no section headings" is the cheapest
  way to keep it from creeping back;
* a contribution kind the catalogue has never seen still produces a card,
  which is what "the plugin layer is a distribution mechanism" has to mean.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.plugins.uninstall as removal
from app.document.builtin_formats import builtin_registry
from app.document.formats import DocumentFormat, FormatConflictError
from app.plugins import loader
from app.plugins.catalog import PluginCatalog
from app.plugins.contributions import (
    KIND_DOCUMENT_FORMAT,
    KIND_PLUGIN,
    KIND_REMOTE_SOURCE,
    DocumentFormatContribution,
    PluginHost,
    RemoteSourceContribution,
    install_contribution,
)
from app.plugins.loader import load_plugins
from app.plugins.manifest import PluginManifest, merge_keywords
from app.plugins.records import (
    SOURCE_BUILTIN,
    SOURCE_DIRECTORY,
    SOURCE_DISTRIBUTION,
    PluginRecord,
)
from app.plugins.state import PluginStateStore
from app.remote.credentials import (
    CredentialChannelSpec,
    CredentialStore,
    ProviderCredentialSpec,
)
from app.remote.notifications import NotificationHub, NotificationTarget
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry


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
    identity = ProviderIdentity(
        name="acme-chat", label="Acme Chat", icon="bell", tag="通知"
    )

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
                tag="知识库",
            ),
            CredentialChannelSpec(
                name="api",
                label="Acme API",
                has_secret=True,
                default_secret_ref="acme:token",
                summary="用令牌读取 Acme",
                icon="key",
                keywords=("token",),
                tag="知识库",
            ),
        ),
    )


def _host(*contributions, store: CredentialStore) -> PluginHost:
    """A host with the given contributions installed, exactly as the app does.

    Built through ``install_contribution`` rather than by registering directly,
    so the tests exercise the same path a plugin takes.
    """
    host = PluginHost(
        providers=ProviderRegistry(store),
        formats=builtin_registry(),
        credentials=store,
        notifications=NotificationHub(),
    )
    for contribution in contributions:
        install_contribution(host, contribution)
    return host


def _remote(provider, *, spec: ProviderCredentialSpec | None = None) -> RemoteSourceContribution:
    return RemoteSourceContribution(
        provider=provider,
        credential_spec=spec if spec is not None else _spec(),
        always_configured=True,
    )


def _catalog(*providers, store: CredentialStore) -> PluginCatalog:
    return PluginCatalog(_host(*(_remote(provider) for provider in providers), store=store))


# -- derivation -------------------------------------------------------------


def test_one_card_per_channel_not_per_provider(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)

    manifests = catalog.manifests()

    # The whole point of the design: 「Acme 网页登录」 and 「Acme API」 are two
    # things a user chooses between, so they are two cards.
    assert [m.id for m in manifests] == ["acme:web", "acme:api"]
    assert [m.label for m in manifests] == ["Acme 网页登录", "Acme API"]
    assert {m.provider_label for m in manifests} == {"Acme Docs"}
    assert {m.kind for m in manifests} == {KIND_REMOTE_SOURCE}


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


def test_tag_is_declared_by_the_channel_not_derived_from_purpose(store: CredentialStore) -> None:
    manifests = _catalog(_DocsProvider(), store=store).manifests()

    # The display text is copy the plugin owns; the page must not have to know
    # what a purpose is called.
    assert [m.tag for m in manifests] == ["知识库", "知识库"]


def test_provider_without_credential_channels_still_gets_a_card(store: CredentialStore) -> None:
    host = _host(
        RemoteSourceContribution(provider=_ChattyProvider(), always_configured=True),
        store=store,
    )

    manifests = PluginCatalog(host).manifests()

    # Otherwise a plugin needing no stored secret would be invisible in settings
    # even though it works — and the user could not log into it from the UI.
    assert [m.id for m in manifests] == ["acme-chat:core"]
    assert manifests[0].has_secret is False
    assert manifests[0].icon == "bell"
    assert manifests[0].tag == "通知"


def test_a_channel_may_override_the_providers_icon(store: CredentialStore) -> None:
    # A bot webhook is not a library even though it ships inside a docs plugin,
    # which is why the override exists.
    spec = ProviderCredentialSpec(
        provider="acme",
        channels=(
            CredentialChannelSpec(
                name="bot", label="Acme 机器人", has_secret=False, icon="bell", tag="通知"
            ),
        ),
    )
    catalog = PluginCatalog(_host(_remote(_DocsProvider(), spec=spec), store=store))

    assert catalog.manifests()[0].icon == "bell"


def test_channel_without_icon_falls_back_to_the_provider(store: CredentialStore) -> None:
    spec = ProviderCredentialSpec(
        provider="acme",
        channels=(CredentialChannelSpec(name="plain", label="Acme", has_secret=False),),
    )
    catalog = PluginCatalog(_host(_remote(_DocsProvider(), spec=spec), store=store))
    manifest = catalog.manifests()[0]

    assert manifest.icon == "library"
    # A channel that declares no tag renders none — no fallback invents copy.
    assert manifest.tag is None


def test_card_reflects_live_credential_state(store: CredentialStore) -> None:
    catalog = _catalog(_DocsProvider(), store=store)
    assert catalog.manifests()[1].state == "disconnected"

    store.save_secret("acme", "api", "t0ken", "acme:token")
    store.mark_state("acme", "api", "verified", account_label="alice")

    manifest = catalog.manifests()[1]
    assert manifest.configured is True
    assert manifest.state == "verified"
    assert manifest.account_label == "alice"


# -- the document_format kind -----------------------------------------------


def _tex_format() -> DocumentFormat:
    return DocumentFormat(
        name="tex",
        label="TeX 文档",
        media_type="text/x-tex",
        extensions=(".tex", ".latex"),
        parse=lambda document: document,  # type: ignore[arg-type,return-value]
    )


def _tex_contribution() -> DocumentFormatContribution:
    return DocumentFormatContribution(
        format=_tex_format(),
        label="TeX 文档",
        summary="把 TeX 源文件导入 DocMind",
        tag="文档格式",
        icon="library",
        version="0.1.0",
    )


def test_a_format_contribution_installs_into_the_format_registry(store: CredentialStore) -> None:
    host = _host(_tex_contribution(), store=store)

    # Installing is all it takes for the importer to accept the format.
    assert host.formats.for_extension(".tex").name == "tex"
    assert ".tex" in host.formats.pickable_extensions()


def test_a_format_contribution_produces_a_card(store: CredentialStore) -> None:
    manifest = PluginCatalog(_host(_tex_contribution(), store=store)).manifests()[0]

    assert manifest.kind == KIND_DOCUMENT_FORMAT
    assert manifest.id == "tex:core"
    assert manifest.label == "TeX 文档"
    assert manifest.tag == "文档格式"
    assert manifest.version == "0.1.0"
    # The suffixes are carried so the card can say what it adds.
    assert manifest.extensions == (".tex", ".latex")
    # A format plugs into nothing, so there is no owner to name.
    assert manifest.provider_label is None
    # And it stores no credential, so the credential fields stay at defaults.
    assert manifest.has_secret is False
    assert manifest.state == "disconnected"


def test_a_format_conflict_fails_the_installation_and_records_nothing(
    store: CredentialStore,
) -> None:
    """A plugin must not be able to take a suffix the user already relies on."""
    squatter = DocumentFormatContribution(
        format=DocumentFormat(
            name="evil",
            label="Evil PDF",
            media_type="text/x-evil",
            extensions=(".pdf",),
            parse=lambda document: document,  # type: ignore[arg-type,return-value]
        ),
        label="Evil PDF",
    )
    host = _host(store=store)

    with pytest.raises(FormatConflictError):
        install_contribution(host, squatter)

    # Nothing half-applied: the built-in still wins and no card was recorded.
    assert host.formats.for_extension(".pdf").name == "pdf"
    assert PluginCatalog(host).manifests() == []


def test_contributions_of_both_kinds_share_one_flat_catalogue(store: CredentialStore) -> None:
    catalog = PluginCatalog(_host(_remote(_DocsProvider()), _tex_contribution(), store=store))

    manifests = catalog.manifests()

    # Two cards from the remote provider, one from the format — and no grouping
    # between them, because `kind` is metadata rather than a section.
    assert [m.id for m in manifests] == ["acme:web", "acme:api", "tex:core"]
    assert [m.kind for m in manifests] == [
        KIND_REMOTE_SOURCE,
        KIND_REMOTE_SOURCE,
        KIND_DOCUMENT_FORMAT,
    ]


def test_the_catalogue_never_branches_on_kind(store: CredentialStore) -> None:
    """A contribution kind this codebase has never seen still yields a card.

    If the catalogue (or the manifest) needed a matching edit for a new kind,
    the extensibility claim would be false and this test is what noticed.
    """
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Invented:
        label: str

        kind = "invented_kind"

        def install(self, host: PluginHost) -> None:
            return None

        def cards(self, host: PluginHost) -> list[PluginManifest]:
            return [
                PluginManifest(
                    id=f"invented:{self.label}",
                    kind=self.kind,
                    provider="invented",
                    channel=self.label,
                    label=f"发明的 {self.label}",
                )
            ]

    catalog = PluginCatalog(_host(_Invented(label="widget"), store=store))

    manifests = catalog.manifests()

    assert [m.kind for m in manifests] == ["invented_kind"]
    assert manifests[0].label == "发明的 widget"
    # And it is searchable by its own declared text, like any other card.
    assert [m.id for m in catalog.search("发明的")] == ["invented:widget"]


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


def test_search_finds_a_format_by_its_extension(store: CredentialStore) -> None:
    # A user looking for "latex" must find the card that adds .latex; the suffix
    # is declared data and belongs in the haystack.
    catalog = PluginCatalog(_host(_tex_contribution(), store=store))

    assert [m.id for m in catalog.search(".tex")] == ["tex:core"]
    assert [m.id for m in catalog.search("latex")] == ["tex:core"]
    assert [m.id for m in catalog.search("文档格式")] == ["tex:core"]
    # The card's id, kind and provider are searchable too, which is what keeps
    # this side of the rule identical to `plugin-filter.ts` rather than merely
    # similar — the two lists are hand-kept and this is what notices a drift.
    assert [m.id for m in catalog.search("document_format")] == ["tex:core"]
    assert [m.id for m in catalog.search("tex:core")] == ["tex:core"]


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
    assert body[0]["kind"] == KIND_REMOTE_SOURCE
    assert body[0]["tag"] == "知识库"


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


def test_third_party_plugin_registers_onto_the_shared_host(
    store: CredentialStore, monkeypatch
) -> None:
    host = _host(store=store)
    monkeypatch.setattr(
        loader,
        "_installed_candidates",
        lambda: (
            _FakeEntryPoint(
                "acme",
                RemoteSourceContribution(
                    provider=_DocsProvider(),
                    credential_spec=_spec(),
                    notifications=(_notification_target("acme:bot"),),
                ),
            ),
        ),
    )

    diagnostics = load_plugins(host)

    assert diagnostics.failed == []
    assert host.providers.names() == ["acme"]
    # A plugin's notification target lands in the same hub as the built-in ones,
    # so business code never learns where it came from.
    assert "acme:bot" in set(host.notifications.names())
    # And its card appears with no UI change at all.
    assert [m.id for m in PluginCatalog(host).manifests()] == ["acme:web", "acme:api"]


def test_a_plugin_may_return_several_contributions(store: CredentialStore, monkeypatch) -> None:
    """One package adding two different kinds is the case the layer exists for."""
    host = _host(store=store)
    monkeypatch.setattr(
        loader,
        "_installed_candidates",
        lambda: (_FakeEntryPoint("multi", [_remote(_DocsProvider()), _tex_contribution()]),),
    )

    diagnostics = load_plugins(host)

    assert diagnostics.failed == []
    assert host.providers.names() == ["acme"]
    assert host.formats.for_extension(".tex").name == "tex"
    assert [m.id for m in PluginCatalog(host).manifests()] == ["acme:web", "acme:api", "tex:core"]


def test_a_broken_plugin_is_reported_and_skipped(store: CredentialStore, monkeypatch) -> None:
    host = _host(store=store)
    monkeypatch.setattr(
        loader,
        "_installed_candidates",
        lambda: (
            _FakeEntryPoint("broken", None, raises=True),
            _FakeEntryPoint("wrong-shape", ["not a contribution"]),
            _FakeEntryPoint("ok", _remote(_DocsProvider())),
        ),
    )

    diagnostics = load_plugins(host)

    # A missing integration is a degraded feature; refusing to boot is not an
    # acceptable response to one. The good plugin still loads.
    assert [item["name"] for item in diagnostics.failed] == ["broken", "wrong-shape"]
    assert host.providers.names() == ["acme"]
    assert PluginCatalog(host).manifests()


def test_a_plugin_whose_format_conflicts_is_reported_not_ignored(
    store: CredentialStore, monkeypatch
) -> None:
    """Silently losing a suffix is indistinguishable from never installing."""
    host = _host(store=store)
    monkeypatch.setattr(
        loader,
        "_installed_candidates",
        lambda: (
            _FakeEntryPoint(
                "squatter",
                DocumentFormatContribution(
                    format=DocumentFormat(
                        name="evil",
                        label="Evil PDF",
                        media_type="text/x-evil",
                        extensions=(".pdf",),
                        parse=lambda document: document,  # type: ignore[arg-type,return-value]
                    ),
                    label="Evil PDF",
                ),
            ),
        ),
    )

    diagnostics = load_plugins(host)

    assert [item["name"] for item in diagnostics.failed] == ["squatter"]
    assert ".pdf" in next(item["error"] for item in diagnostics.failed)
    assert host.formats.for_extension(".pdf").name == "pdf"
    # Nothing half-applied — but the plugin that failed is a row rather than
    # nothing at all. A plugin that vanished on failure would be indistinguishable
    # from one that was never installed, which is the same reasoning the
    # diagnostics channel exists for, applied to the page this time.
    rows = PluginCatalog(host).manifests()
    assert [m.kind for m in rows] == [KIND_PLUGIN]
    assert rows[0].origin is not None
    assert rows[0].origin.error is not None and ".pdf" in rows[0].origin.error


def test_diagnostics_endpoint_exposes_failures(client: TestClient, auth_headers) -> None:
    class _Diag:
        def as_dicts(self):
            return [{"name": "broken", "error": "boom"}]

    client.app.state.plugin_discovery = _Diag()

    response = client.get("/api/plugins/diagnostics", headers=auth_headers)

    assert response.status_code == 200, response.text
    # Surfaced, not logged-and-forgotten: a plugin that silently failed looks
    # identical to one that was never installed.
    assert response.json() == [{"name": "broken", "error": "boom"}]


def test_manifest_view_is_the_wire_contract(store: CredentialStore) -> None:
    manifest = _catalog(_DocsProvider(), store=store).manifests()[0]

    view = manifest.view()

    assert view.id == "acme:web"
    assert view.kind == KIND_REMOTE_SOURCE
    assert view.provider_label == "Acme Docs"
    assert view.keywords


def test_no_plugins_yields_an_empty_catalogue(store: CredentialStore) -> None:
    host = _host(store=store)

    assert PluginCatalog(host).manifests() == []
    assert PluginManifest is not None


# -- extensibility: the contract a new plugin author relies on --------------


def test_a_vendor_absent_from_this_codebase_gets_a_full_card(store: CredentialStore) -> None:
    """The one promise the settings page makes to a plugin author.

    A provider invented here — named nowhere in this repository, installed the
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

    host = _host(
        RemoteSourceContribution(
            provider=_Zenith(),
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
                        tag="知识库",
                    ),
                ),
            ),
        ),
        store=store,
    )
    catalog = PluginCatalog(host)

    manifests = catalog.manifests()

    # Every string the card renders is plugin-declared, so the renderer needs no
    # per-vendor knowledge to draw it.
    assert [m.id for m in manifests] == ["zenith:api"]
    assert manifests[0].label == "Zenith API"
    assert manifests[0].provider_label == "Zenith Board"
    assert manifests[0].summary == "用令牌读取 Zenith"
    assert manifests[0].icon == "key"
    assert manifests[0].version == "9.9.9"
    # And it is findable by a term that appears in no label.
    assert [m.id for m in catalog.search("工单")] == ["zenith:api"]


def test_keywords_are_merged_without_duplicates(store: CredentialStore) -> None:
    """A provider whose own keywords repeat its name must not ship it twice.

    The provider name is prepended to every manifest, and repeating it in
    ``keywords`` is the most natural thing an author can write — so this is the
    default shape, not an exotic one. Duplicates are invisible on the card but
    inflate the search haystack, where they cost a redundant substring check per
    keystroke.
    """
    # Dirty input on purpose: the name appears in the provider keywords AND in
    # the channel keywords AND is prepended by the catalogue.
    host = _host(
        RemoteSourceContribution(
            provider=_DocsProvider(),
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
        ),
        store=store,
    )
    catalog = PluginCatalog(host)

    keywords = catalog.manifests()[0].keywords

    # Order preserved and first spelling kept: the channel's own aliases lead,
    # the provider-wide ones follow, and the name — prepended by the catalogue —
    # collapses into the channel's own mention of it. Case-insensitive, so a
    # shouty "ACME" cannot sneak past as a second term.
    assert keywords == ("acme", "token", "wiki")
    # Every term still searchable — merging must not drop anything.
    assert [m.id for m in catalog.search("wiki")] == ["acme:api"]
    assert [m.id for m in catalog.search("ACME")] == ["acme:api"]


def test_merging_keywords_tolerates_missing_and_empty_groups() -> None:
    assert merge_keywords((), ()) == ()
    assert merge_keywords(("a",), ()) == ("a",)
    assert merge_keywords((), ("b",)) == ("b",)
    assert merge_keywords(None, ("c",)) == ("c",)
    assert merge_keywords(("a", "a"), ("A", "b")) == ("a", "b")


def test_plugin_directory_endpoint_reports_the_data_directory(
    client: TestClient, auth_headers, app_settings
) -> None:
    """Installing a plugin is a filesystem action, so the path must come from
    the server: it is derived from the data directory, which is per-platform and
    follows the application's own name."""
    response = client.get("/api/plugins/directory", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"path": str(app_settings.plugins_dir)}
    # A path under the data directory, not somewhere the packaged build cannot
    # write to.
    assert app_settings.plugins_dir.parent == app_settings.data_dir
    assert app_settings.plugins_dir.is_dir()


def test_plugin_directory_endpoint_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/plugins/directory").status_code == 401


# -- where a row came from ---------------------------------------------------
#
# Every row names the plugin behind it, because the plugin is the unit the page
# can act on: 「语雀 API」 is not something a user can switch off, the plugin that
# declares it is. These tests pin the three provenances and, more importantly,
# what each one may be offered.


def _installed(host: PluginHost, contribution, record: PluginRecord) -> None:
    install_contribution(host, contribution, plugin=record)


def test_a_card_names_the_plugin_it_came_from(store: CredentialStore) -> None:
    host = _host(store=store)
    _installed(
        host,
        _tex_contribution(),
        PluginRecord(
            name="docmind-tex",
            source=SOURCE_DIRECTORY,
            path=Path("/plugins/docmind-tex"),
            version="0.1.0",
        ),
    )

    manifest = PluginCatalog(host).manifests()[0]

    assert manifest.origin is not None
    assert manifest.origin.plugin == "docmind-tex"
    assert manifest.origin.path == "/plugins/docmind-tex"
    assert manifest.origin.version == "0.1.0"
    assert manifest.origin.enabled is True
    assert manifest.origin.active is True
    # A directory is the one copy of a plugin the user put there, so it is the
    # only one both actions apply to.
    assert manifest.origin.toggleable is True
    assert manifest.origin.removable is True


def test_a_built_in_integration_offers_no_switch(store: CredentialStore) -> None:
    host = _host(store=store)
    _installed(host, _remote(_DocsProvider()), PluginRecord(name="acme", source=SOURCE_BUILTIN))

    origin = PluginCatalog(host).manifests()[0].origin

    assert origin is not None
    assert origin.source == SOURCE_BUILTIN
    # Installed by the application, not found on disk: there is nothing here to
    # switch off or take out, and a button that cannot work is worse than an
    # absent one.
    assert origin.toggleable is False
    assert origin.removable is False


def test_an_installed_distribution_can_be_switched_off_but_not_removed(
    store: CredentialStore,
) -> None:
    host = _host(store=store)
    _installed(
        host,
        _tex_contribution(),
        PluginRecord(name="docmind-tex", source=SOURCE_DISTRIBUTION),
    )

    origin = PluginCatalog(host).manifests()[0].origin

    assert origin is not None
    assert origin.toggleable is True
    # The packaged runtime ships no installer, so there is nothing that could
    # remove it and the page must not imply otherwise.
    assert origin.removable is False
    assert origin.path is None


def test_a_contribution_with_no_plugin_to_name_offers_no_plugin_action(
    store: CredentialStore,
) -> None:
    """Dirty input: a contribution installed straight onto a host.

    There is nothing to attribute it to, so the page shows no provenance line
    and no action — which beats guessing what it was part of.
    """
    host = _host(_remote(_DocsProvider()), store=store)

    manifest = PluginCatalog(host).manifests()[0]

    assert manifest.origin is None
    assert manifest.id == "acme:web"


def test_a_switched_off_plugin_is_a_row_of_its_own(store: CredentialStore) -> None:
    """A switched-off plugin has no cards, so it has to be a row built from its
    own record — otherwise nothing on the page could switch it back on."""
    host = _host(_remote(_DocsProvider()), store=store)
    host.plugins.append(
        PluginRecord(
            name="docmind-tex",
            source=SOURCE_DIRECTORY,
            path=Path("/plugins/docmind-tex"),
            label="docmind-tex",
            summary="把 LaTeX 源文件导入 DocMind",
            version="0.1.0",
            disabled=True,
        )
    )

    rows = PluginCatalog(host).manifests()

    assert [m.id for m in rows] == ["acme:web", "acme:api", "docmind-tex@directory"]
    off = rows[-1]
    assert off.kind == KIND_PLUGIN
    assert off.label == "docmind-tex"
    assert off.summary == "把 LaTeX 源文件导入 DocMind"
    assert off.origin is not None
    assert off.origin.enabled is False
    assert off.origin.active is False
    assert off.origin.error is None
    # And it is still findable — by its own name, which appears in no label of
    # any card it used to contribute.
    assert [m.id for m in PluginCatalog(host).search("docmind-tex")] == [
        "docmind-tex@directory"
    ]
    assert [m.id for m in PluginCatalog(host).search("LaTeX")] == ["docmind-tex@directory"]


def test_a_card_is_findable_by_its_plugins_name(store: CredentialStore) -> None:
    """The renderer's filter is kept in step with this rule by hand."""
    host = _host(store=store)
    _installed(
        host,
        _tex_contribution(),
        PluginRecord(name="docmind-tex", source=SOURCE_DIRECTORY, path=Path("/plugins/tex")),
    )

    assert [m.id for m in PluginCatalog(host).search("docmind-tex")] == ["tex:core"]


# -- the two writes ----------------------------------------------------------


def _known(client: TestClient, tmp_path, *records: PluginRecord) -> PluginStateStore:
    """Put plugins and a preference file on a running app.

    The app is built once per test session with production wiring, so a test
    injects what discovery would have found rather than building a second app
    with a plugin directory on disk — the discovery side is covered directly in
    ``tests/plugins``.
    """
    state = PluginStateStore(tmp_path / "state")
    client.app.state.plugin_state = state
    for record in records:
        client.app.state.plugin_host.plugins.append(record)
    return state


def _directory_plugin(tmp_path, name: str = "docmind-tex") -> PluginRecord:
    return PluginRecord(name=name, source=SOURCE_DIRECTORY, path=tmp_path / "plugins" / name)


def test_switching_a_plugin_off_is_persisted_and_says_a_restart_is_needed(
    client: TestClient, auth_headers, tmp_path
) -> None:
    state = _known(client, tmp_path, _directory_plugin(tmp_path))

    response = client.put(
        "/api/plugins/docmind-tex/enabled", json={"enabled": False}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    # `restartRequired` is stated rather than left for the page to know:
    # discovery runs once per process, so nothing the user just changed is live
    # yet, and a silent response would read as a switch that did nothing.
    assert response.json() == {
        "plugin": "docmind-tex",
        "enabled": False,
        "restartRequired": True,
        "removedTo": None,
    }
    assert state.is_disabled("docmind-tex")


def test_switching_a_plugin_off_is_visible_without_a_restart(
    client: TestClient, auth_headers, tmp_path
) -> None:
    """The served catalogue reflects the switch immediately.

    Rows are derived from the records this process holds, so a switch that only
    wrote the preference file would leave the card reading 「已启用」 until the
    next start — and the honest "takes effect at the next start" notice would
    never appear, because the state it keys off never changed.
    """
    host = client.app.state.plugin_host
    record = PluginRecord(
        name="docmind-tex",
        source=SOURCE_DIRECTORY,
        path=tmp_path / "plugins" / "docmind-tex",
    )
    install_contribution(host, _tex_contribution(), plugin=record)
    _known(client, tmp_path)

    response = client.put(
        "/api/plugins/docmind-tex/enabled", json={"enabled": False}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    card = PluginCatalog(host).manifests()[0]
    assert card.id == "tex:core"
    assert card.origin is not None
    assert card.origin.enabled is False
    assert card.origin.active is False
    # Its card stays. The plugin *is* still loaded in this process, so dropping
    # it here would be the same lie in the other direction.
    assert card.kind == KIND_DOCUMENT_FORMAT


def test_switching_a_plugin_back_on_is_visible_too(
    client: TestClient, auth_headers, tmp_path
) -> None:
    """Dirty input: the switch is moved twice in one session."""
    host = client.app.state.plugin_host
    record = PluginRecord(
        name="docmind-tex", source=SOURCE_DIRECTORY, path=tmp_path / "plugins" / "docmind-tex"
    )
    install_contribution(host, _tex_contribution(), plugin=record)
    _known(client, tmp_path)

    client.put("/api/plugins/docmind-tex/enabled", json={"enabled": False}, headers=auth_headers)
    client.put("/api/plugins/docmind-tex/enabled", json={"enabled": True}, headers=auth_headers)

    origin = PluginCatalog(host).manifests()[0].origin
    assert origin is not None
    assert origin.enabled is True
    assert origin.active is True


def test_a_plugin_can_be_switched_back_on(client: TestClient, auth_headers, tmp_path) -> None:
    state = _known(client, tmp_path, _directory_plugin(tmp_path))
    state.set_enabled("docmind-tex", enabled=False)

    response = client.put(
        "/api/plugins/docmind-tex/enabled", json={"enabled": True}, headers=auth_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True
    assert state.disabled() == frozenset()


def test_the_switch_refuses_a_built_in(client: TestClient, auth_headers, tmp_path) -> None:
    _known(client, tmp_path, PluginRecord(name="yuque", source=SOURCE_BUILTIN))

    response = client.put(
        "/api/plugins/yuque/enabled", json={"enabled": False}, headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PLUGIN_NOT_TOGGLEABLE"


def test_the_switch_refuses_a_plugin_nobody_installed(
    client: TestClient, auth_headers, tmp_path
) -> None:
    """Dirty input: a name that is not a plugin.

    The name is resolved against discovery, never used as given — this is the
    step that keeps "switch a plugin off" from being "write any name into the
    preference file", and it is why the path a removal moves comes from our own
    scan rather than from the request.
    """
    state = _known(client, tmp_path)

    response = client.put(
        "/api/plugins/not-a-plugin/enabled", json={"enabled": False}, headers=auth_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_UNKNOWN"
    assert state.disabled() == frozenset()


def test_a_name_that_looks_like_a_path_is_still_just_a_name(
    client: TestClient, auth_headers, tmp_path
) -> None:
    """Dirty input: a traversal-shaped plugin name.

    Whatever the router makes of it, the answer is the same — 404 or a plain
    refusal, never a file operation. Asserted on the preference file rather than
    on a status code, because the property under test is that nothing was acted
    on.
    """
    state = _known(client, tmp_path, _directory_plugin(tmp_path))

    client.put(
        "/api/plugins/..%2F..%2Fetc%2Fpasswd/enabled",
        json={"enabled": False},
        headers=auth_headers,
    )

    assert state.disabled() == frozenset()


def test_removing_a_plugin_moves_its_directory_and_drops_its_switch(
    client: TestClient, auth_headers, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins_dir = client.app.state.settings.plugins_dir
    root = plugins_dir / "docmind-tex"
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'docmind-tex'\n", encoding="utf-8")
    graveyard = tmp_path / "graveyard"
    monkeypatch.setattr(removal, "_container", lambda data_dir: graveyard)
    state = _known(
        client,
        tmp_path,
        PluginRecord(
            name="docmind-tex", source=SOURCE_DIRECTORY, path=root, disabled=True
        ),
    )
    state.set_enabled("docmind-tex", enabled=False)

    response = client.delete("/api/plugins/docmind-tex", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["restartRequired"] is True
    # Where it went, rather than a claim about the trash: the destination
    # differs per platform, and the user needs to know where to look.
    assert body["removedTo"] == str(graveyard / "docmind-tex")
    assert not root.exists()
    assert (graveyard / "docmind-tex" / "pyproject.toml").is_file()
    # The switch goes with the files. A name left behind would make a later
    # install of the same plugin start out switched off.
    assert state.disabled() == frozenset()
    # And the row goes with them: continuing to list a plugin whose files are
    # gone would offer a remove button that now fails.
    assert [
        m.id
        for m in PluginCatalog(client.app.state.plugin_host).manifests()
        if m.origin is not None and m.origin.plugin == "docmind-tex"
    ] == []


def test_removing_a_distribution_is_refused_with_a_reason(
    client: TestClient, auth_headers, tmp_path
) -> None:
    _known(client, tmp_path, PluginRecord(name="docmind-tex", source=SOURCE_DISTRIBUTION))

    response = client.delete("/api/plugins/docmind-tex", headers=auth_headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PLUGIN_NOT_REMOVABLE"


def test_removing_a_built_in_is_refused(client: TestClient, auth_headers, tmp_path) -> None:
    _known(client, tmp_path, PluginRecord(name="yuque", source=SOURCE_BUILTIN))

    response = client.delete("/api/plugins/yuque", headers=auth_headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PLUGIN_NOT_REMOVABLE"


def test_the_switch_endpoints_require_a_token(client: TestClient, tmp_path) -> None:
    _known(client, tmp_path, _directory_plugin(tmp_path))

    assert (
        client.put("/api/plugins/docmind-tex/enabled", json={"enabled": False}).status_code == 401
    )
    assert client.delete("/api/plugins/docmind-tex").status_code == 401


def test_a_plugin_list_with_no_switch_file_reports_unavailable(
    client: TestClient, auth_headers, tmp_path
) -> None:
    """A build with no preference file has nothing to write the switch into.

    Reported rather than swallowed: a switch that silently did nothing is
    indistinguishable from one that worked, and the user would leave the page
    believing a plugin had been turned off.
    """
    _known(client, tmp_path, _directory_plugin(tmp_path))
    client.app.state.plugin_state = None

    response = client.put(
        "/api/plugins/docmind-tex/enabled", json={"enabled": False}, headers=auth_headers
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PLUGIN_CATALOG_UNAVAILABLE"
