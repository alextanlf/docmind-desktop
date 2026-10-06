from __future__ import annotations

import asyncio
import gc
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from app.api.embedding import prepare_embedding
from app.config import AppSettings, EmbeddingSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.secrets import MemorySecretStore
from app.main import create_app
from app.schemas.embedding import ModelStatus


def test_embedding_routes_require_runtime_token(client: TestClient) -> None:
    assert client.get("/api/embedding/status").status_code == 401
    assert client.post("/api/embedding/prepare").status_code == 401


def _embedding_state(client: TestClient, headers: dict[str, str], attempts: int = 50) -> str:
    """轮询直到状态离开初始的 unavailable/downloading。

    lifespan 里的预热是 create_task，TestClient 通过 portal 在另一线程跑事件循环，
    所以启动返回后模型**可能**还没加载完 —— 这里等它稳定，不赌调度顺序。
    """
    state = "unavailable"
    for _ in range(attempts):
        state = client.get("/api/embedding/status", headers=headers).json()["state"]
        if state not in {"unavailable", "downloading"}:
            return state
        time.sleep(0.02)
    return state


def test_startup_prewarms_embedding_without_any_user_action(tmp_path) -> None:
    """应用启动即应把嵌入模型加载进内存，不需要用户去设置页点任何按钮。

    嵌入是索引期硬依赖，且模型随应用分发，所以「加载」不是用户该操心的步骤。
    environment="production" 是关键 —— lifespan 对 test 环境刻意跳过预热，
    否则本仓库所有 FakeEmbeddingProvider 相关的调用计数断言都会被污染。
    """
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"),
        data_dir=tmp_path / "data",
        environment="production",
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8), preparation_delay=True)
    headers = {"X-DocMind-Token": "test-runtime-token"}
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        # 用户一次都没点过任何按钮，模型就已经自己加载完了。
        assert _embedding_state(client, headers) == "ready"
        assert fake_embedding.ensure_ready_calls == 1


def test_startup_prewarm_failure_does_not_block_the_app(tmp_path) -> None:
    """预热失败不能拖垮启动：真正用到嵌入的路径会再兜底一次并抛 503。"""

    class FailingEmbeddingProvider:
        def __init__(self) -> None:
            self.status = ModelStatus(
                state="unavailable", model_name="test-model", message="模型尚未准备"
            )

        async def ensure_ready(self) -> ModelStatus:
            self.status = ModelStatus(
                state="error", model_name="test-model", message="嵌入模型准备失败"
            )
            raise ValueError("dimension mismatch")

    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"),
        data_dir=tmp_path / "data",
        environment="production",
    )
    headers = {"X-DocMind-Token": "test-runtime-token"}
    with TestClient(
        create_app(
            settings, secret_store=MemorySecretStore(), embedding_provider=FailingEmbeddingProvider()
        )
    ) as client:
        # 应用照常可用（能响应请求），只是嵌入处于 error。
        assert _embedding_state(client, headers) == "error"


def test_prepare_endpoint_still_retries_after_a_failed_warmup(tmp_path) -> None:
    """启动预热失败后，用户显式重试仍应能重新拉起加载（不再有设置页按钮的兜底）。"""
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        response = client.post("/api/embedding/prepare", headers=headers)
        assert response.status_code == 202
        assert fake_embedding.ensure_ready_calls == 1


def test_embedding_prepare_is_explicit(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        assert client.get("/api/embedding/status", headers=headers).json()["state"] == "unavailable"
        response = client.post("/api/embedding/prepare", headers=headers)
        assert response.status_code == 202
        assert fake_embedding.ensure_ready_calls == 1


def test_second_prepare_request_reuses_existing_preparation_task(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8), preparation_delay=True)
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        client.post("/api/embedding/prepare", headers=headers)
        response = client.post("/api/embedding/prepare", headers=headers)
        assert response.status_code == 202
        assert fake_embedding.ensure_ready_calls == 1


def test_completed_prepare_request_returns_current_status_without_restarting(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("test-runtime-token"), data_dir=tmp_path / "data", environment="test"
    )
    fake_embedding = FakeEmbeddingProvider(EmbeddingSettings(dimension=8))
    with TestClient(
        create_app(settings, secret_store=MemorySecretStore(), embedding_provider=fake_embedding)
    ) as client:
        headers = {"X-DocMind-Token": "test-runtime-token"}
        client.post("/api/embedding/prepare", headers=headers)
        response = client.post("/api/embedding/prepare", headers=headers)

        assert response.status_code == 202
        assert response.json()["state"] == "ready"
        assert fake_embedding.ensure_ready_calls == 1


async def test_failed_background_preparation_consumes_its_terminal_exception() -> None:
    class FailingEmbeddingProvider:
        def __init__(self) -> None:
            self.status = ModelStatus(
                state="unavailable", model_name="test-model", message="模型尚未准备"
            )

        async def ensure_ready(self) -> ModelStatus:
            self.status = ModelStatus(
                state="error", model_name="test-model", message="嵌入模型准备失败"
            )
            raise ValueError("dimension mismatch")

    provider = FailingEmbeddingProvider()
    state = SimpleNamespace(embedding_provider=provider, embedding_prepare_task=None)
    request = Request({"type": "http", "app": SimpleNamespace(state=state)})
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    try:
        await prepare_embedding(request)
        task = state.embedding_prepare_task
        await asyncio.sleep(0)
        assert task.done()
        assert provider.status.state == "error"

        state.embedding_prepare_task = None
        del task
        gc.collect()
        await asyncio.sleep(0)

        assert not any(
            context.get("message") == "Task exception was never retrieved"
            for context in contexts
        )
    finally:
        loop.set_exception_handler(previous_handler)
