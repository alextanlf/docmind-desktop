from __future__ import annotations

import pytest

from app.api.errors import DomainError
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.remote.registry import ProviderRegistry


class StubProvider:
    def __init__(self, name: str, *, label: str = "标签", capabilities=None) -> None:
        self.identity = ProviderIdentity(
            name=name,
            label=label,
            capabilities=capabilities or ProviderCapabilities(),
        )
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class NamedProvider:
    """A provider that only exposes ``name`` (legacy shape)."""

    name = "legacy"

    async def close(self) -> None:
        return None


class NamelessProvider:
    async def close(self) -> None:
        return None


def test_register_lists_providers_sorted_by_name() -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider("yuque"))
    registry.register(StubProvider("feishu"))

    assert registry.names() == ["feishu", "yuque"]


def test_register_uses_the_name_attribute_when_identity_is_absent() -> None:
    registry = ProviderRegistry()
    registry.register(NamedProvider(), always_configured=True)

    assert registry.names() == ["legacy"]


def test_register_requires_a_name() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ValueError):
        registry.register(NamelessProvider())


def test_register_rejects_duplicate_names() -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider("yuque"))

    with pytest.raises(ValueError):
        registry.register(StubProvider("yuque"))


def test_get_returns_the_registered_provider_and_rejects_unknown_names() -> None:
    registry = ProviderRegistry()
    provider = StubProvider("yuque")
    registry.register(provider)

    assert registry.get("yuque") is provider

    with pytest.raises(DomainError) as error:
        registry.get("feishu")
    assert error.value.code == "REMOTE_PROVIDER_UNKNOWN"
    assert error.value.status_code == 404


def test_is_configured_prefers_always_configured_and_probes_otherwise() -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider("yuque"), lambda: False)
    registry.register(StubProvider("feishu"), lambda: True)
    registry.register(StubProvider("fake"), always_configured=True)

    assert registry.is_configured("yuque") is False
    assert registry.is_configured("feishu") is True
    assert registry.is_configured("fake") is True
    assert registry.is_configured("missing") is False


def test_is_configured_swallows_probe_failures() -> None:
    registry = ProviderRegistry()

    def explode() -> bool:
        raise RuntimeError("probe failed")

    registry.register(StubProvider("yuque"), explode)

    assert registry.is_configured("yuque") is False
    assert registry.any_configured() is False


def test_any_configured_and_first_configured_scan_in_name_order() -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider("aaa"), lambda: False)
    configured = StubProvider("yuque")
    registry.register(configured, lambda: True)

    assert registry.any_configured() is True
    assert registry.first_configured() is configured


def test_first_configured_is_none_when_nothing_is_configured() -> None:
    registry = ProviderRegistry()
    registry.register(StubProvider("yuque"), lambda: False)

    assert registry.first_configured() is None


def test_summaries_expose_label_configured_and_capabilities() -> None:
    registry = ProviderRegistry()
    registry.register(
        StubProvider(
            "yuque",
            label="语雀",
            capabilities=ProviderCapabilities(browser_install=True, marker_lookup=True),
        ),
        lambda: True,
    )
    registry.register(StubProvider("feishu", label="飞书"), lambda: False)

    summaries = registry.summaries()

    assert [summary.name for summary in summaries] == ["feishu", "yuque"]
    yuque = summaries[1]
    assert yuque.label == "语雀"
    assert yuque.configured is True
    assert yuque.capabilities.browser_install is True
    assert yuque.capabilities.marker_lookup is True
    assert summaries[0].capabilities.browser_install is False


@pytest.mark.asyncio
async def test_close_all_closes_every_provider() -> None:
    registry = ProviderRegistry()
    first = StubProvider("yuque")
    second = StubProvider("feishu")
    registry.register(first)
    registry.register(second)

    await registry.close_all()

    assert first.closed == 1
    assert second.closed == 1


@pytest.mark.asyncio
async def test_close_all_tolerates_provider_failures() -> None:
    class ExplodingProvider(StubProvider):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    registry = ProviderRegistry()
    registry.register(ExplodingProvider("yuque"))
    healthy = StubProvider("feishu")
    registry.register(healthy)

    await registry.close_all()

    assert healthy.closed == 1
