from __future__ import annotations


def test_validation_errors_use_simplified_chinese_without_english_leakage(client, auth_headers) -> None:
    """Catches default Pydantic English validation details leaking to users."""
    response = client.post("/api/repositories", headers=auth_headers, json={"name": " "})
    too_long = client.post("/api/repositories", headers=auth_headers, json={"name": "x" * 121})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "String should have at most" not in str(response.json())
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "INVALID_REQUEST"
