def test_probe(caplog):
    import logging
    from app.yuque.session import inject, StoredCookie
    from app.api.errors import DomainError
    from tests.yuque.test_session import _FakeDriver
    d = _FakeDriver(); d.rejected.add("_yuque_session")
    with caplog.at_level("WARNING"):
        try:
            inject(d, [StoredCookie("_yuque_session","a",".yuque.com","/")])
        except DomainError:
            pass
    print("records:", [(r.name, r.levelname, r.getMessage()[:50]) for r in caplog.records])
    print("caplog.handler level:", caplog.handler.level)
    assert False, "show me"
