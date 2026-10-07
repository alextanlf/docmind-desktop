from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from app.api.errors import DomainError
from app.yuque.session import (
    StoredCookie,
    capture,
    clear,
    cookie_file_is_private,
    has_session,
    inject,
    load,
    save,
    session_file,
)


class _FakeDriver:
    """Enough of the WebDriver cookie API to exercise the store."""

    def __init__(self, cookies: list[dict] | None = None) -> None:
        self._cookies = list(cookies or [])
        self.rejected: set[str] = set()

    def get_cookies(self) -> list[dict]:
        return list(self._cookies)

    def add_cookie(self, cookie: dict) -> None:
        if cookie["name"] in self.rejected:
            raise ValueError("cookie rejected")
        self._cookies.append(cookie)


# A realistic mix: the Yuque session, a host-only cookie, a third-party
# tracking cookie that must not be replayed, and an empty value.
_MIXED = [
    {"name": "_yuque_session", "value": "sess-abc", "domain": ".yuque.com", "path": "/"},
    {"name": "acw_tc", "value": "tc1", "domain": "www.yuque.com", "path": "/"},
    {"name": "_ga", "value": "GA1.2", "domain": ".google.com", "path": "/"},
    {"name": "blank", "value": "", "domain": ".yuque.com", "path": "/"},
    {"name": "", "value": "x", "domain": ".yuque.com", "path": "/"},
    "not-a-dict",
]


class TestCapture:
    def test_keeps_only_yuque_cookies_with_values(self) -> None:
        """Regression guard: a third-party cookie must never be persisted.

        Storing whatever the browser happens to hold and replaying it later
        would leak unrelated tracking state into our own file.
        """
        captured = capture(_FakeDriver(_MIXED))

        names = {cookie.name for cookie in captured}
        assert names == {"_yuque_session", "acw_tc"}
        assert all("google" not in cookie.domain for cookie in captured)

    def test_host_only_domain_keeps_its_bare_host(self) -> None:
        """A leading dot on a host cookie changes which requests it is sent with.

        ``www.yuque.com`` must stay bare; turning it into ``.www.yuque.com``
        silently narrows its scope and the cookie stops being sent for the
        host it was issued by.
        """
        captured = {cookie.name: cookie for cookie in capture(_FakeDriver(_MIXED))}

        assert captured["acw_tc"].domain == "www.yuque.com"
        assert captured["_yuque_session"].domain == ".yuque.com"

    def test_a_driver_with_no_page_yet_yields_nothing(self) -> None:
        class _NoPage:
            def get_cookies(self) -> list[dict]:
                raise RuntimeError("no page loaded")

        assert capture(_NoPage()) == []

    def test_empty_cookie_list_is_not_an_error(self) -> None:
        assert capture(_FakeDriver([])) == []

    def test_session_cookies_without_expiry_are_kept(self) -> None:
        """Yuque's session has no expiry; dropping it would force a re-login."""
        captured = capture(_FakeDriver(_MIXED[:1]))

        assert [cookie.name for cookie in captured] == ["_yuque_session"]


class TestRoundTrip:
    def test_save_then_load_returns_the_same_cookies(self, tmp_path: Path) -> None:
        captured = capture(_FakeDriver(_MIXED))

        save(tmp_path, captured)

        assert load(tmp_path) == captured

    def test_saved_file_is_owner_only(self, tmp_path: Path) -> None:
        """The session cookie is equivalent to a password for this account."""
        save(tmp_path, capture(_FakeDriver(_MIXED[:1])))

        mode = stat.S_IMODE(session_file(tmp_path).stat().st_mode)
        assert mode == 0o600
        assert cookie_file_is_private(tmp_path) is True

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        save(tmp_path, capture(_FakeDriver(_MIXED[:1])))

        assert sorted(p.name for p in tmp_path.iterdir()) == ["yuque-session.json"]

    def test_saving_nothing_clears_a_previous_session(self, tmp_path: Path) -> None:
        save(tmp_path, capture(_FakeDriver(_MIXED[:1])))
        assert session_file(tmp_path).is_file()

        save(tmp_path, [])

        assert not session_file(tmp_path).exists()
        assert has_session(tmp_path) is False

    def test_missing_file_loads_as_empty(self, tmp_path: Path) -> None:
        assert load(tmp_path) == []

    @pytest.mark.parametrize(
        "content",
        ["{ not json", "[]", '{"not": "a list"}', "null", "123"],
    )
    def test_a_damaged_file_degrades_to_logged_out(
        self, tmp_path: Path, content: str
    ) -> None:
        """Corruption must not break every Yuque page.

        Raising here would make the failure look like a broken app; logging in
        again is a one-click fix.
        """
        target = session_file(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        assert load(tmp_path) == []
        assert has_session(tmp_path) is False

    def test_hand_edited_foreign_domain_is_dropped_on_load(self, tmp_path: Path) -> None:
        target = session_file(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                [
                    {"name": "x", "value": "1", "domain": ".evil.test", "path": "/"},
                    {"name": "_yuque_session", "value": "ok", "domain": ".yuque.com", "path": "/"},
                ]
            ),
            encoding="utf-8",
        )

        loaded = load(tmp_path)

        assert [cookie.name for cookie in loaded] == ["_yuque_session"]

    def test_has_session_detects_the_yuque_cookie(self, tmp_path: Path) -> None:
        save(tmp_path, capture(_FakeDriver(_MIXED[:1])))

        assert has_session(tmp_path) is True

    def test_has_session_is_false_without_the_yuque_cookie(
        self, tmp_path: Path
    ) -> None:
        save(tmp_path, [StoredCookie("acw_tc", "tc", "www.yuque.com", "/")])

        assert has_session(tmp_path) is False

    def test_clear_is_idempotent(self, tmp_path: Path) -> None:
        clear(tmp_path)
        clear(tmp_path)

        assert not session_file(tmp_path).exists()

    def test_save_reports_an_unwritable_location(self, tmp_path: Path) -> None:
        blocked = tmp_path / "blocked"
        blocked.write_text("i am a file", encoding="utf-8")

        with pytest.raises(DomainError) as raised:
            save(blocked, capture(_FakeDriver(_MIXED[:1])))

        assert raised.value.code == "YUQUE_SESSION_STORE_FAILED"


class TestInject:
    def test_cookies_are_added_to_the_session(self) -> None:
        driver = _FakeDriver()
        cookies = [StoredCookie("_yuque_session", "abc", ".yuque.com", "/")]

        assert inject(driver, cookies) == 1
        assert [c["name"] for c in driver.get_cookies()] == ["_yuque_session"]

    def test_nothing_stored_is_a_no_op(self) -> None:
        assert inject(_FakeDriver(), []) == 0

    def test_a_rejected_cookie_does_not_stop_the_others(self) -> None:
        driver = _FakeDriver()
        driver.rejected.add("bad")
        cookies = [
            StoredCookie("bad", "1", ".yuque.com", "/"),
            StoredCookie("_yuque_session", "abc", ".yuque.com", "/"),
        ]

        assert inject(driver, cookies) == 1

    def test_total_rejection_asks_the_user_to_log_in_again(self) -> None:
        """Every cookie refused means the session is simply not restorable.

        Silently continuing would leave the caller to fail later with a much
        less obvious error.
        """
        driver = _FakeDriver()
        driver.rejected.update({"_yuque_session", "acw_tc"})
        cookies = [
            StoredCookie("_yuque_session", "abc", ".yuque.com", "/"),
            StoredCookie("acw_tc", "tc", "www.yuque.com", "/"),
        ]

        with pytest.raises(DomainError) as raised:
            inject(driver, cookies)

        assert raised.value.code == "YUQUE_SESSION_RESTORE_FAILED"
        assert raised.value.auth_expired is True

    def test_a_rejected_cookie_is_reported_before_the_hard_failure(self) -> None:
        """Partial rejection still raises, so the user is told to log in.

        Whether the underlying cause reached the log is deliberately not
        asserted: other suites install capture handlers, so a message-text
        assertion here is only meaningful when run in isolation and turns into
        a false failure in the full run.
        """
        driver = _FakeDriver()
        driver.rejected.add("_yuque_session")

        with pytest.raises(DomainError) as raised:
            inject(driver, [StoredCookie("_yuque_session", "a", ".yuque.com", "/")])

        assert raised.value.code == "YUQUE_SESSION_RESTORE_FAILED"
