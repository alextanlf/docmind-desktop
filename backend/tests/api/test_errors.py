import pytest


def test_domain_error_uses_stable_envelope(client, auth_headers):
    response = client.get("/_test/domain-error", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TEST_CONFLICT"


def test_startup_rejects_non_fixed_port(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Fails if startup ever accepts an environment-overridden listening port."""
    monkeypatch.setenv("DOCMIND_SESSION_TOKEN", "test-runtime-token")
    monkeypatch.setenv("DOCMIND_DATA_DIR", str(tmp_path / "docmind-data"))
    monkeypatch.setenv("DOCMIND_PORT", "8080")

    from app.__main__ import main

    with pytest.raises(RuntimeError, match="DocMind 后端只能绑定到 127.0.0.1:18900"):
        main()
