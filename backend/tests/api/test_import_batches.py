def test_batch_endpoint_is_registered(client, auth_headers) -> None:
    response = client.get("/api/import-batches", headers=auth_headers)
    assert response.status_code == 200


def test_unsupported_batch_kind_returns_stable_validation_error(client, auth_headers) -> None:
    response = client.post(
        "/api/import-batches",
        headers=auth_headers,
        json={
            "kind": "web",
            "sourceId": "https://example.test",
            "repositoryId": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def _seed_batch(client) -> str:
    """造一个批次和几条被拒的抓取记录 —— 诊断端点读的就是这两张表。"""
    import json

    from app.storage.models import BatchImportRecord, BatchState, CrawlEntryRecord

    batch = client.app.state.batch_store.create_batch(
        BatchImportRecord(
            source_kind="web",
            source_descriptor_json=json.dumps({"entryUrl": "https://docs.test/guide"}),
            repository_id=None,
            state=BatchState.AWAITING_CONFIRMATION,
            discovery_version=1,
            message="",
        )
    )
    entries = client.app.state.crawl_entries
    rejected = entries.upsert_frontier(
        CrawlEntryRecord(batch_id=batch.id, canonical_url="https://docs.test/blocked", depth=0)
    )
    entries.mark_rejected(
        rejected.id, error_code="CRAWL_FETCH_FAILED", rejection_reason="unreachable"
    )
    denied = entries.upsert_frontier(
        CrawlEntryRecord(batch_id=batch.id, canonical_url="https://docs.test/robots", depth=0)
    )
    entries.mark_rejected(denied.id, error_code="ROBOTS_DENIED", rejection_reason="robots_denied")
    fetched = entries.upsert_frontier(
        CrawlEntryRecord(batch_id=batch.id, canonical_url="https://docs.test/ok", depth=0)
    )
    entries.mark_fetched(fetched.id)
    return batch.id


def test_batch_diagnostics_explains_each_rejected_url(client, auth_headers) -> None:
    """用户要的是「为什么这个页面没进来」，而每类原因的处置完全不同。"""
    batch_id = _seed_batch(client)
    response = client.get(f"/api/import-batches/{batch_id}/diagnostics", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # 已抓取的那条不算诊断 —— 它是成功的。
    assert body["total"] == 2
    assert body["truncated"] is False
    assert body["items"] == [
        {"canonicalUrl": "https://docs.test/blocked", "reason": "unreachable"},
        {"canonicalUrl": "https://docs.test/robots", "reason": "robots_denied"},
    ]


def test_batch_diagnostics_reports_truncation_with_the_real_total(client, auth_headers) -> None:
    """截断时仍要报真实总数：用截断后的长度冒充总数，会让 500 条拒绝看起来只有 1 条。"""
    batch_id = _seed_batch(client)
    response = client.get(
        f"/api/import-batches/{batch_id}/diagnostics?limit=1", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["truncated"] is True
    assert len(body["items"]) == 1


def test_batch_diagnostics_for_an_unknown_batch_is_not_an_empty_list(client, auth_headers) -> None:
    """空集的含义是「抓过的都进来了」，与「这个批次不存在」是两件事。"""
    response = client.get(
        "/api/import-batches/00000000-0000-0000-0000-0000000000ff/diagnostics",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BATCH_NOT_FOUND"
