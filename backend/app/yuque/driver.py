"""Fetch and cache the chromedriver matching the installed browser.

Selenium 4.6+ ships a "Selenium Manager" that is supposed to do this
automatically. It is deliberately not used: on a stock macOS machine it
reports ``NoSuchDriverException`` without ever explaining that it failed to
detect the browser version, and the failure looks identical to "no network".
Downloading a driver is ~9 MB and deterministic, so we do it ourselves and
can report a precise reason when something goes wrong.

Layout under the app data dir::

    drivers/chromedriver/<major>/chromedriver[.exe]

Versioning by *major* only is intentional: Chrome's driver compatibility is
guaranteed per major version, and pinning the full version would strand users
on a driver that stops working after every browser update.
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from app.yuque.browser import (
    BrowserInstallation,
    driver_major_version,
)

# Where Chrome for Testing publishes the version index. Keyed by nothing --
# one index serves every platform, and each entry lists all of its downloads.
_VERSION_INDEX_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
)
_DOWNLOAD_BASE = "https://storage.googleapis.com/chrome-for-testing-public"
_INDEX_TIMEOUT_SECONDS = 20.0
_DOWNLOAD_TIMEOUT_SECONDS = 120.0


class DriverUnavailableError(RuntimeError):
    """The driver could not be provided, with a message meant for the user."""


@dataclass(frozen=True)
class DriverBundle:
    """A ready-to-use driver executable."""

    path: Path
    major_version: int
    # True when the driver was already present, so the UI can say "已就绪"
    # instead of "已下载" and avoid implying work it did not do.
    cached: bool = False


def _platform_key() -> str | None:
    """Map the running interpreter to a Chrome-for-Testing platform name."""
    machine = platform.machine().lower()
    if os.name == "nt":
        return (
            "win64"
            if machine.endswith("64") or machine in {"amd64", "x86_64"}
            else "win32"
        )
    if os.name == "posix" and sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "mac-arm64"
        return "mac-x64"
    if machine in {"x86_64", "amd64"}:
        return "linux64"
    return None


def driver_cache_path(root: Path, major: int) -> Path:
    name = "chromedriver.exe" if os.name == "nt" else "chromedriver"
    return root / "drivers" / "chromedriver" / str(major) / name


async def ensure_driver(
    browser: BrowserInstallation, cache_root: Path
) -> DriverBundle:
    """Return a driver matching ``browser``, downloading it only if needed.

    The cached copy is validated by *running* it, because a truncated download
    or a file left behind by an older app version would otherwise surface as an
    opaque WebDriver timeout much later.
    """
    target = driver_cache_path(cache_root, browser.major_version)
    if target.is_file():
        if driver_major_version(target) == browser.major_version:
            return DriverBundle(target, browser.major_version, cached=True)
        # A stale or corrupt cache entry must not be reused; drop it and
        # re-download rather than surfacing a confusing driver mismatch.
        _force_remove(target)

    url = await _resolve_driver_url(browser.major_version)
    archive = await _download(url)
    extracted = _extract_driver(archive, target)
    _ensure_executable(extracted)

    actual = driver_major_version(extracted)
    if actual != browser.major_version:
        _force_remove(extracted)
        raise DriverUnavailableError(
            f"驱动版本不匹配：浏览器为 {browser.major_version}，"
            f"驱动为 {actual}。请重试。"
        )
    return DriverBundle(extracted, browser.major_version, cached=False)


async def _resolve_driver_url(major: int) -> str:
    """Look up the download URL for the driver of exactly ``major``.

    The "last known good" index only lists the current channel tips, so a user
    on, say, Chrome 154 would be handed the 157 driver — which then refuses to
    attach. The per-version endpoint is queried instead so the driver major
    always matches the browser major, falling back to the channel index only
    when the exact build has been retired from the CDN.
    """
    key = _platform_key()
    if key is None:
        raise DriverUnavailableError(
            "当前系统暂不支持自动获取浏览器驱动，请改用语雀 API 连接。"
        )
    payload = await _fetch_json(
        "https://googlechromelabs.github.io/chrome-for-testing/"
        "known-good-versions-with-downloads.json"
    )
    exact = _matching_release(payload, major)
    if exact is not None:
        url = _driver_url(exact, key)
        if url:
            return url

    # The exact build is gone; take the channel tip only if it is at least the
    # requested major, so we never hand back an older driver than the browser.
    tip = await _fetch_json(_VERSION_INDEX_URL)
    for release in _releases_descending(tip):
        if _version_major(release.get("version", "")) < major:
            continue
        url = _driver_url(release, key)
        if url:
            return url
    raise DriverUnavailableError(
        f"未找到适配当前系统的驱动（浏览器主版本 {major}）。"
    )


async def _fetch_json(url: str) -> dict:
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_INDEX_TIMEOUT_SECONDS), follow_redirects=True
        ) as client:
            response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as error:
        raise DriverUnavailableError(
            "无法获取驱动版本信息，请检查网络后重试。"
        ) from error
    except (ValueError, KeyError) as error:
        raise DriverUnavailableError("驱动版本信息格式异常，请稍后重试。") from error
    if not isinstance(payload, dict):
        raise DriverUnavailableError("驱动版本信息格式异常，请稍后重试。")
    return payload


def _version_major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return -1


def _matching_release(payload: dict, major: int) -> dict | None:
    for release in _releases_descending(payload):
        if _version_major(release.get("version", "")) == major:
            return release
    return None


def _driver_url(release: dict, platform_key: str) -> str | None:
    for item in release.get("downloads", {}).get("chromedriver", []):
        if item.get("platform") == platform_key:
            return str(item.get("url") or "") or None
    return None


def _releases_descending(payload: dict) -> list[dict]:
    """Every listed release, newest first.

    Two index shapes exist and both have to work: the per-version endpoint
    returns a flat ``versions`` array (~2500 entries), while the channel
    endpoint returns a ``channels`` object keyed by Stable/Beta/Dev/Canary.
    Reading only one of them silently yields no candidates at all.
    """
    releases: list[dict] = []

    listed = payload.get("versions")
    if isinstance(listed, list):
        releases.extend(item for item in listed if isinstance(item, dict))

    channels = payload.get("channels")
    if isinstance(channels, dict):
        releases.extend(
            item
            for item in channels.values()
            if isinstance(item, dict) and item.get("version")
        )

    return sorted(
        (item for item in releases if item.get("version")),
        key=lambda item: _version_tuple(item["version"]),
        reverse=True,
    )


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(version).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


async def _download(url: str) -> Path:
    """Fetch the driver archive into a temporary file and return its path."""
    target = Path(tempfile.gettempdir()) / "chromedriver-download.zip"
    try:
        async with (
            httpx.AsyncClient(
                timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT_SECONDS), follow_redirects=True
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
    except httpx.HTTPError as error:
        _force_remove(target)
        raise DriverUnavailableError(
            "驱动下载失败，请检查网络后重试。"
        ) from error
    return target


def _extract_driver(archive: Path, target: Path) -> Path:
    """Pull the driver executable out of the platform archive.

    Copying the bytes with ``copyfileobj`` loses the archive's permission
    bits, and the published Chrome for Testing zips mark the driver
    executable while some extractors drop that on the floor — leaving a file
    that reports no version and looks like a broken download. The mode is
    therefore read back out of the zip entry and reapplied.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            member = next(
                (
                    info
                    for info in bundle.infolist()
                    if not info.is_dir() and _is_driver_member(info.filename)
                ),
                None,
            )
            if member is None:
                raise DriverUnavailableError("驱动压缩包内容异常，请重试。")
            with bundle.open(member) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            _apply_zip_mode(member, target)
    except zipfile.BadZipFile as error:
        _force_remove(target)
        raise DriverUnavailableError("驱动下载内容损坏，请重试。") from error
    finally:
        _force_remove(archive)
    return target


def _is_driver_member(filename: str) -> bool:
    """True only for the driver binary itself.

    A plain ``endswith("chromedriver")`` also matches the archive's
    ``LICENSE.chromedriver`` and ``THIRD_PARTY_NOTICES.chromedriver``, which
    are text files; extracting one of those yields an "Exec format error"
    that looks like a corrupt download.
    """
    return PurePosixPath(filename).name in {"chromedriver", "chromedriver.exe"}


def _apply_zip_mode(info: zipfile.ZipInfo, target: Path) -> None:
    """Restore the executable bit recorded in the archive.

    Windows does not need this (and does not have the bit), so a zeroed mode
    there is left alone rather than being second-guessed.
    """
    if os.name == "nt":
        return
    mode = info.external_attr >> 16
    if not mode:
        return
    try:
        target.chmod(mode)
    except OSError:
        # Some filesystems reject chmod; the explicit pass in
        # ``_ensure_executable`` is the backstop.
        pass


def _ensure_executable(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    except OSError as error:
        raise DriverUnavailableError("驱动文件不可执行，请检查目录权限。") from error


def _force_remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def install_driver(browser: BrowserInstallation, cache_root: Path) -> DriverBundle:
    """User-facing "install browser driver" action; always re-downloads.

    A manual retry after a failure must not be short-circuited by a cached
    copy that a previous attempt wrote but that turned out to be unusable.
    """
    target = driver_cache_path(cache_root, browser.major_version)
    _force_remove(target)
    return await ensure_driver(browser, cache_root)
