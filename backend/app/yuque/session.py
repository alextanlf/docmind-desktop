"""Persist the Yuque session across WebDriver sessions.

Playwright's ``launch_persistent_context(user_data_dir)`` kept the login in a
browser profile on disk, so nothing else had to be done. WebDriver has no
equivalent: every session starts with a clean profile, so the session cookie
has to be saved after login and re-injected on the next run.

The store is deliberately narrow:

* only cookies for the Yuque domains are persisted, so a redirect through a
  third-party host cannot smuggle a value into the file we later replay;
* only the four fields WebDriver actually needs are kept, dropping the
  bookkeeping fields (host-only flag, priority, sameSite) that would
  otherwise round-trip into a different shape;
* the file is written with owner-only permissions, because the session cookie
  is equivalent to a password for this account.

Session cookies (those without an expiry) are persisted too. Yuque's session
is one of them, and dropping it would mean the user has to log in again every
time the app restarts — which is exactly the behaviour this module exists to
prevent.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from app.api.errors import DomainError

logger = logging.getLogger(__name__)

# Only these hosts may contribute cookies. Yuque serves the app from www and
# scopes the session to the parent domain, so both are needed.
_ALLOWED_DOMAIN_SUFFIXES = (".yuque.com", "yuque.com")
_COOKIE_FILE_NAME = "yuque-session.json"
_FILE_MODE = 0o600


@dataclass(frozen=True)
class StoredCookie:
    """The subset of a WebDriver cookie that survives a round trip."""

    name: str
    value: str
    domain: str
    path: str


def _is_yuque_domain(domain: str) -> bool:
    normalized = (domain or "").lstrip(".").lower()
    return any(
        normalized == suffix.lstrip(".") or normalized.endswith(suffix.lstrip("."))
        for suffix in _ALLOWED_DOMAIN_SUFFIXES
    )


def session_file(browser_data_dir: Path) -> Path:
    return browser_data_dir / _COOKIE_FILE_NAME


def capture(driver: object) -> list[StoredCookie]:
    """Read the Yuque cookies out of a live driver session.

    A driver that has not navigated anywhere yet raises on ``get_cookies``;
    that is treated as "nothing to save" rather than an error, because the
    caller may be probing a page that never loaded.
    """
    try:
        raw = driver.get_cookies()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a session with no page yet has no cookies
        return []

    captured: list[StoredCookie] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        domain = str(item.get("domain") or "")
        value = str(item.get("value") or "")
        if not name or not value or not _is_yuque_domain(domain):
            continue
        captured.append(
            StoredCookie(
                name=name,
                value=value,
                # Selenium reports a domain cookie as ".yuque.com" and a
                # host-only cookie as "www.yuque.com". The leading dot must
                # NOT be added to the latter: ".www.yuque.com" would only match
                # that exact host and its subdomains, silently dropping the
                # cookie for www.yuque.com itself.
                domain=domain,
                path=str(item.get("path") or "/"),
            )
        )
    return captured


def save(browser_data_dir: Path, cookies: list[StoredCookie]) -> None:
    """Persist the session, or clear it when there is nothing to keep."""
    if not cookies:
        clear(browser_data_dir)
        return
    try:
        browser_data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        # A *file* sitting where the profile directory should be is the common
        # case here, and mkdir raises before _write_private gets a chance to
        # translate it into a readable message.
        raise DomainError(
            "YUQUE_SESSION_STORE_FAILED",
            "无法保存语雀登录状态，请检查目录权限",
            500,
            True,
        ) from error
    target = session_file(browser_data_dir)
    payload = json.dumps(
        [asdict(cookie) for cookie in cookies], ensure_ascii=False, indent=2
    )
    _write_private(target, payload)


def load(browser_data_dir: Path) -> list[StoredCookie]:
    """Read the stored session, or an empty list when there is nothing usable.

    A corrupt or hand-edited file is treated as "not logged in" instead of
    raising: the user can always fix it by logging in again, whereas an
    exception here would break every Yuque page instead of just the session.
    """
    target = session_file(browser_data_dir)
    if not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    restored: list[StoredCookie] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "")
        if not name or not value or not _is_yuque_domain(domain):
            continue
        restored.append(
            StoredCookie(
                name=name,
                value=value,
                # Preserved verbatim, for the same reason as in ``capture``:
                # adding a leading dot to a host-only cookie changes which
                # requests it is sent with.
                domain=domain,
                path=str(item.get("path") or "/"),
            )
        )
    return restored


def clear(browser_data_dir: Path) -> None:
    """Forget the stored session (used on logout and on a failed login)."""
    target = session_file(browser_data_dir)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        # Losing the file is not fatal: the worst case is a stale session that
        # the next login check will reject.
        pass


def has_session(browser_data_dir: Path) -> bool:
    return any(cookie.name == "_yuque_session" for cookie in load(browser_data_dir))


def inject(driver: object, cookies: list[StoredCookie]) -> int:
    """Add the stored cookies to a session that already sits on the site.

    WebDriver refuses ``add_cookie`` for a domain the browser is not currently
    on, so the caller must have navigated to Yuque first. Returns how many
    cookies were accepted; individual rejects are not fatal because a browser
    may legitimately drop one it considers malformed — but if *none* are
    accepted the session is not restorable and the user has to log in again.
    """
    if not cookies:
        return 0
    added = 0
    for cookie in cookies:
        try:
            driver.add_cookie(  # type: ignore[attr-defined]
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                }
            )
        except Exception:
            # Logged rather than silently skipped: a browser rejecting every
            # cookie is the difference between "restored" and "needs login",
            # and swallowing it hides why.
            logger.warning(
                "注入语雀 cookie 失败：%s（域 %s）", cookie.name, cookie.domain, exc_info=True
            )
            continue
        added += 1
    if added == 0:
        raise DomainError(
            "YUQUE_SESSION_RESTORE_FAILED",
            "语雀登录状态无法恢复，请重新登录",
            401,
            False,
            "重新登录语雀",
            auth_expired=True,
        )
    return added


def _write_private(target: Path, payload: str) -> None:
    """Write owner-only, replacing atomically.

    The file holds a live session credential, so a partially written or
    world-readable copy is not acceptable: write to a sibling and rename.
    """
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, _FILE_MODE)
        temporary.replace(target)
    except OSError as error:
        raise DomainError(
            "YUQUE_SESSION_STORE_FAILED",
            "无法保存语雀登录状态，请检查目录权限",
            500,
            True,
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def cookie_file_is_private(browser_data_dir: Path) -> bool:
    """Whether the stored session file is owner-only (test/diagnostic helper)."""
    target = session_file(browser_data_dir)
    if not target.is_file():
        return True
    return stat.S_IMODE(target.stat().st_mode) == _FILE_MODE
