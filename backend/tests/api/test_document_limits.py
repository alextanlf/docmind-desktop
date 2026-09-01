from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import AppSettings
from app.core.embedding import FakeEmbeddingProvider
from app.core.secrets import MemorySecretStore
from app.main import create_app
from app.schemas.documents import DocumentInput
from app.yuque.gateway import FakeYuqueGateway


def test_document_content_preserves_whitespace_at_the_character_boundary() -> None:
    content = "\n" + ("x" * 1_999_998) + " "

    validated = DocumentInput(title="Guide", content=content)

    assert validated.content == content
    assert len(validated.content) == 2_000_000


def test_document_content_rejects_more_than_two_million_characters() -> None:
    with pytest.raises(ValidationError):
        DocumentInput(title="Guide", content="x" * 2_000_001)


def test_document_content_rejects_more_than_four_mib_of_utf8() -> None:
    content = "中" * 1_398_102
    assert len(content) < 2_000_000
    assert len(content.encode("utf-8")) == 4_194_306

    with pytest.raises(ValidationError):
        DocumentInput(title="Guide", content=content)


def test_request_body_limit_returns_fixed_error_without_reflecting_content(tmp_path) -> None:
    settings = AppSettings(
        session_token=SecretStr("token"),
        data_dir=tmp_path / "data",
        environment="test",
        max_request_body_bytes=64,
    )
    secret = "do-not-reflect-this-secret"
    app = create_app(
        settings,
        secret_store=MemorySecretStore(),
        embedding_provider=FakeEmbeddingProvider(settings.embedding_settings),
        yuque_gateway=FakeYuqueGateway(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/repositories",
            headers={
                "Content-Type": "application/json",
                "X-DocMind-Token": "token",
            },
            content=(f'{{"name":"{secret * 4}"}}').encode(),
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    assert secret not in response.text
