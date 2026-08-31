def test_health_rejects_missing_runtime_token(client):
    response = client.get("/health")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "AUTH_REQUIRED",
            "message": "缺少或无效的本地会话令牌",
            "retryable": False,
            "action": None,
        }
    }


def test_health_accepts_runtime_token(client, auth_headers):
    response = client.get("/health", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}
