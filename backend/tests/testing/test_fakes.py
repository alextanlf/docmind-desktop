from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.errors import DomainError
from app.config import AppSettings
from app.core.llm import ChatRequest, LLMMessage, ModelConfig, OpenAICompatibleProvider
from app.core.secrets import MemorySecretStore


def test_fake_services_are_selected_only_for_nonproduction_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DOCMIND_FAKE_SERVICES", "1")
    from app.core.embedding import FakeEmbeddingProvider
    from app.main import create_app
    from app.testing.fakes import FakeLLMProvider, FakeYuqueGateway

    app = create_app(
        AppSettings(
            session_token=SecretStr("test-token"),
            data_dir=tmp_path / "fake-data",
            environment="test",
        )
    )

    assert type(app.state.secret_store).__name__ == "MemorySecretStore"
    assert isinstance(app.state.embedding_provider, FakeEmbeddingProvider)
    assert isinstance(app.state.yuque_gateway, FakeYuqueGateway)
    assert isinstance(app.state.fake_llm_provider, FakeLLMProvider)


def test_fake_services_are_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DOCMIND_FAKE_SERVICES", "1")
    from app.main import create_app

    with pytest.raises(ValueError, match="fake services"):
        create_app(
            AppSettings(
                session_token=SecretStr("test-token"),
                data_dir=tmp_path / "production-data",
                environment="production",
            )
        )


async def test_fake_llm_streams_three_context_words_with_a_citation() -> None:
    from app.testing.fakes import FakeLLMProvider

    provider = FakeLLMProvider(delay_seconds=0)
    request = ChatRequest(
        messages=[
            LLMMessage(
                role="user",
                content="文档片段：\n[S1]\n内容：@State 管理视图拥有的状态。\n\n问题：@State 有什么作用？",
            )
        ]
    )

    deltas = [delta.content async for delta in provider.stream_chat(request)]

    assert len(deltas) == 3
    assert "[S1]" in "".join(deltas)
    assert "@State" in "".join(deltas)


async def test_fake_llm_refuses_to_answer_without_context() -> None:
    from app.testing.fakes import FakeLLMProvider

    provider = FakeLLMProvider(delay_seconds=0)
    request = ChatRequest(messages=[LLMMessage(role="user", content="问题：@State 有什么作用？")])

    assert [delta.content async for delta in provider.stream_chat(request)] == []


async def test_fake_services_use_the_same_llm_for_model_connection_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DOCMIND_FAKE_SERVICES", "1")
    from app.main import create_app

    app = create_app(
        AppSettings(
            session_token=SecretStr("test-token"),
            data_dir=tmp_path / "fake-data",
            environment="test",
        )
    )

    with TestClient(app):
        provider = app.state.settings_service.provider_factory(
            ModelConfig(
                preset="custom", base_url="https://example.test/v1", model="test", timeout_seconds=1
            ),
            "fake-key",
        )

        assert provider is app.state.fake_llm_provider
        result = await provider.test_connection()
        assert result.connected is True
        assert result.latency_ms == 0


def test_nonfake_services_keep_the_default_model_connection_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("DOCMIND_FAKE_SERVICES", raising=False)
    from app.main import create_app

    app = create_app(
        AppSettings(
            session_token=SecretStr("test-token"),
            data_dir=tmp_path / "production-like-data",
            environment="test",
        ),
        secret_store=MemorySecretStore(),
    )

    with TestClient(app):
        assert app.state.settings_service.provider_factory is OpenAICompatibleProvider


async def test_fake_service_controls_are_one_shot_and_targeted(tmp_path) -> None:
    from app.testing.fakes import (
        E2EControl,
        E2EControlledFakeEmbeddingProvider,
        FakeLLMProvider,
    )

    control = E2EControl(tmp_path)
    provider = FakeLLMProvider(control=control)
    control.path.parent.mkdir(parents=True)
    control.path.write_text("fail-next-model-test", encoding="utf-8")

    with pytest.raises(DomainError, match="模型服务认证失败"):
        await provider.test_connection()
    assert not control.path.exists()
    assert (await provider.test_connection()).connected is True

    embedding = E2EControlledFakeEmbeddingProvider(
        AppSettings(session_token=SecretStr("token"), data_dir=tmp_path).embedding_settings,
        control=control,
        delay_seconds=0,
    )
    control.path.write_text("fail-next-index", encoding="utf-8")
    await embedding.ensure_ready()
    with pytest.raises(DomainError, match="嵌入模型不可用"):
        await embedding.embed_documents(["example"])
    assert not control.path.exists()

    control.path.write_text("delay-next-import", encoding="utf-8")
    await embedding.ensure_ready()
    assert not control.path.exists()
