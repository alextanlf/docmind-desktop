def test_batch_recovery_service_is_wired(client) -> None:
    assert hasattr(client.app.state, "batch_service")

