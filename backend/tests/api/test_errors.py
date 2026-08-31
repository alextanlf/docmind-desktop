def test_domain_error_uses_stable_envelope(client, auth_headers):
    response = client.get("/_test/domain-error", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TEST_CONFLICT"
