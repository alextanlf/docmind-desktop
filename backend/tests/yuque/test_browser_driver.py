from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import httpx
import pytest

from app.yuque import driver as driver_module
from app.yuque.browser import (
    BrowserInstallation,
    BrowserNotFoundError,
    browser_installed,
    find_browser,
)
from app.yuque.driver import (
    DriverUnavailableError,
    _driver_url,
    _is_driver_member,
    _matching_release,
    _releases_descending,
    _version_major,
    driver_cache_path,
    ensure_driver,
    install_driver,
)

# -- browser discovery ------------------------------------------------------


class TestBrowserDiscovery:
    def test_missing_browser_raises_an_actionable_error(self, monkeypatch) -> None:
        monkeypatch.setattr(driver_module.os, "name", "nt", raising=False)
        monkeypatch.setattr("app.yuque.browser._windows_candidates", list)
        monkeypatch.setattr("app.yuque.browser._darwin_candidates", list)
        monkeypatch.setattr("app.yuque.browser._linux_candidates", list)

        with pytest.raises(BrowserNotFoundError) as raised:
            find_browser()

        # The message has to tell the user what to do, not just that it failed.
        assert "Chrome" in str(raised.value)

    def test_browser_installed_never_raises(self, monkeypatch) -> None:
        def _absent() -> None:
            raise BrowserNotFoundError("no browser")

        monkeypatch.setattr("app.yuque.browser.find_browser", _absent)

        assert browser_installed() is False

    def test_browser_installed_is_true_when_a_browser_exists(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.yuque.browser.find_browser",
            lambda: BrowserInstallation("chrome", "/bin/chrome", 154),
        )

        assert browser_installed() is True

    def test_a_browser_without_a_parsable_version_is_skipped(self, monkeypatch) -> None:
        """A broken shim must not be paired with a driver we cannot trust."""
        monkeypatch.setattr(
            "app.yuque.browser._darwin_candidates",
            lambda: [("chrome", "/bin/chrome")],
        )
        monkeypatch.setattr("app.yuque.browser._windows_candidates", list)
        monkeypatch.setattr("app.yuque.browser._linux_candidates", list)
        monkeypatch.setattr("app.yuque.browser._is_macos", lambda: True)
        monkeypatch.setattr(driver_module.os, "name", "posix", raising=False)
        monkeypatch.setattr(
            "app.yuque.browser._read_major_version", lambda _exe: None
        )

        with pytest.raises(BrowserNotFoundError):
            find_browser()


# -- version index parsing --------------------------------------------------


class TestVersionIndexParsing:
    def test_flat_versions_array_is_read(self) -> None:
        """The per-version endpoint returns a flat array, not ``channels``."""
        payload = {
            "versions": [
                {"version": "154.0.8011.0", "downloads": {}},
                {"version": "155.0.1.0", "downloads": {}},
            ]
        }

        versions = [item["version"] for item in _releases_descending(payload)]

        assert versions == ["155.0.1.0", "154.0.8011.0"]

    def test_channels_object_is_read(self) -> None:
        """The channel endpoint uses a different shape; both must work."""
        payload = {
            "channels": {
                "Stable": {"version": "157.0.8089.0"},
                "Beta": {"version": "158.0.1.0"},
            }
        }

        versions = [item["version"] for item in _releases_descending(payload)]

        assert versions[0] == "158.0.1.0"
        assert "157.0.8089.0" in versions

    def test_exact_major_match_wins_over_other_versions(self) -> None:
        payload = {
            "versions": [
                {"version": "157.0.8089.0"},
                {"version": "154.0.8037.92"},
                {"version": "154.0.8011.0"},
                {"version": "153.0.1.0"},
            ]
        }

        match = _matching_release(payload, 154)

        assert match is not None
        # Newest build of that major, not just any build of it.
        assert match["version"] == "154.0.8037.92"

    def test_no_match_for_an_absent_major(self) -> None:
        assert _matching_release({"versions": [{"version": "157.0.1.0"}]}, 154) is None

    def test_malformed_version_does_not_raise(self) -> None:
        assert _version_major("not-a-version") == -1
        assert _version_major("") == -1
        assert _version_major("154.0.1.0") == 154

    def test_driver_url_is_chosen_by_platform(self) -> None:
        release = {
            "downloads": {
                "chromedriver": [
                    {"platform": "win64", "url": "https://x/win"},
                    {"platform": "mac-arm64", "url": "https://x/mac"},
                ]
            }
        }

        assert _driver_url(release, "mac-arm64") == "https://x/mac"
        assert _driver_url(release, "linux64") is None
        assert _driver_url({}, "mac-arm64") is None


# -- archive member selection -----------------------------------------------


class TestArchiveMemberSelection:
    def test_license_files_are_not_mistaken_for_the_driver(self) -> None:
        """Regression: ``endswith("chromedriver")`` also matched the licence.

        Extracting ``LICENSE.chromedriver`` produced an ``Exec format error``
        that looked like a corrupt download rather than a selection bug.
        """
        assert _is_driver_member("chromedriver-mac-arm64/chromedriver")
        assert _is_driver_member("chromedriver.exe")
        assert not _is_driver_member(
            "chromedriver-mac-arm64/LICENSE.chromedriver"
        )
        assert not _is_driver_member(
            "chromedriver-mac-arm64/THIRD_PARTY_NOTICES.chromedriver"
        )

    @pytest.mark.skipif(os.name == "nt", reason="posix permission bits")
    def test_executable_bit_survives_extraction(self, tmp_path: Path) -> None:
        archive = tmp_path / "d.zip"
        target = tmp_path / "out" / "chromedriver"
        with zipfile.ZipFile(archive, "w") as bundle:
            info = zipfile.ZipInfo("chromedriver-mac-arm64/chromedriver")
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(info, b"#!/bin/sh\necho driver\n")

        extracted = driver_module._extract_driver(archive, target)

        assert extracted.read_bytes().startswith(b"#!/bin/sh")
        # The driver must be runnable straight after extraction; the published
        # zips carry the bit, and losing it is what produced "Exec format error".
        assert os.access(extracted, os.X_OK)

    def test_archive_without_a_driver_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "d.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("chromedriver-mac-arm64/LICENSE.chromedriver", b"text")

        with pytest.raises(DriverUnavailableError):
            driver_module._extract_driver(archive, tmp_path / "out" / "chromedriver")

    def test_corrupt_archive_is_reported_as_such(self, tmp_path: Path) -> None:
        archive = tmp_path / "d.zip"
        archive.write_bytes(b"not a zip at all")

        with pytest.raises(DriverUnavailableError):
            driver_module._extract_driver(archive, tmp_path / "out" / "chromedriver")


# -- caching and version matching ------------------------------------------


def _fake_index(major: int, platform_key: str = "mac-arm64") -> dict:
    return {
        "versions": [
            {
                "version": f"{major}.0.8037.92",
                "downloads": {
                    "chromedriver": [
                        {"platform": platform_key, "url": f"https://x/{major}"}
                    ]
                },
            }
        ]
    }


def _write_fake_driver(path: Path, major: int) -> Path:
    """Write an executable stand-in that reports a version.

    Version detection runs the driver, so a plain text file would raise
    ``Exec format error`` and the test would be asserting the wrong thing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/bin/sh\necho \"ChromeDriver {major}.0.0.0\"\n", encoding="utf-8"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TestDriverCaching:
    async def test_matching_cached_driver_is_reused(self, tmp_path: Path) -> None:
        browser = BrowserInstallation("chrome", "/bin/chrome", 154)
        cached = _write_fake_driver(driver_cache_path(tmp_path, 154), 154)

        bundle = await ensure_driver(browser, tmp_path)

        assert bundle.cached is True
        # Nothing may be downloaded on this path.
        assert bundle.path == cached

    async def test_cached_driver_of_another_major_is_replaced(
        self, tmp_path: Path
    ) -> None:
        browser = BrowserInstallation("chrome", "/bin/chrome", 155)
        stale = _write_fake_driver(driver_cache_path(tmp_path, 155), 149)
        installed = {"count": 0}

        async def _fake_resolve(major: int) -> str:
            installed["count"] += 1
            return "https://example.test/driver.zip"

        async def _fake_download(url: str) -> Path:
            archive = tmp_path / "a.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                info = zipfile.ZipInfo("chromedriver-mac-arm64/chromedriver")
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                bundle.writestr(
                    info, b'#!/bin/sh\necho "ChromeDriver 155.0.0.0"\n'
                )
            return archive

        monkey = pytest.MonkeyPatch()
        monkey.setattr(driver_module, "_resolve_driver_url", _fake_resolve)
        monkey.setattr(driver_module, "_download", _fake_download)
        try:
            bundle = await ensure_driver(browser, tmp_path)
        finally:
            monkey.undo()

        # A driver for a different major must never be handed back.
        assert bundle.major_version == 155
        assert bundle.cached is False
        assert installed["count"] == 1
        # The stale build must be *replaced*, and the replacement has to be
        # the one that was just downloaded. Checking the file content alone
        # would pass even if the old file were never deleted, because
        # extraction overwrites it — so assert the identity of what ran.
        assert stale == bundle.path
        assert bundle.major_version == 155
        assert driver_module.driver_major_version(bundle.path) == 155

    async def test_network_failure_surfaces_a_readable_error(
        self, tmp_path: Path
    ) -> None:
        """A transport error must not leak out as a raw httpx exception.

        Patching ``_resolve_driver_url`` wholesale would skip the very wrapping
        under test, so the failure is injected at the HTTP client instead.
        """
        browser = BrowserInstallation("chrome", "/bin/chrome", 154)

        class _OfflineClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def get(self, *_args, **_kwargs):
                raise httpx.ConnectError("offline")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(
            driver_module.httpx, "AsyncClient", lambda **_kwargs: _OfflineClient()
        )
        try:
            with pytest.raises(DriverUnavailableError) as raised:
                await ensure_driver(browser, tmp_path)
        finally:
            monkey.undo()

        assert "网络" in str(raised.value)

    async def test_install_driver_ignores_a_cached_copy(self, tmp_path: Path) -> None:
        """A manual retry must be able to recover from a bad cached file."""
        browser = BrowserInstallation("chrome", "/bin/chrome", 154)
        cached = _write_fake_driver(driver_cache_path(tmp_path, 154), 0)
        cached.write_text("corrupt", encoding="utf-8")
        resolved = {"count": 0}

        async def _fake_resolve(major: int) -> str:
            resolved["count"] += 1
            return "https://example.test/driver.zip"

        async def _fake_download(_url: str) -> Path:
            archive = tmp_path / "a.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                info = zipfile.ZipInfo("chromedriver-mac-arm64/chromedriver")
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                bundle.writestr(
                    info, b'#!/bin/sh\necho "ChromeDriver 154.0.0.0"\n'
                )
            return archive

        monkey = pytest.MonkeyPatch()
        monkey.setattr(driver_module, "_resolve_driver_url", _fake_resolve)
        monkey.setattr(driver_module, "_download", _fake_download)
        try:
            bundle = await install_driver(browser, tmp_path)
        finally:
            monkey.undo()

        assert bundle.cached is False
        assert resolved["count"] == 1
        # The corrupt entry was replaced by a working driver, not reused.
        assert "154.0.0.0" in cached.read_text(encoding="utf-8")


def test_cache_path_is_versioned_by_major(tmp_path: Path) -> None:
    """Versioning by major keeps a browser update from stranding the cache."""
    assert driver_cache_path(tmp_path, 154) == (
        tmp_path / "drivers" / "chromedriver" / "154" / "chromedriver"
    )
