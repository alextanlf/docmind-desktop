"""Locate a usable Chrome/Chromium on the user's machine.

Why this exists: the Yuque web gateway drives a real browser through
WebDriver, and WebDriver needs a *browser* to attach to. Playwright used to
download its own 170 MB Chromium, which every user paid for whether or not
they used Yuque. Since a desktop OS almost always already has Chrome (or
Chromium) installed, we look for it instead of shipping one.

Scope note: Microsoft Edge is deliberately **not** targeted. Its WebDriver has
to match the Edge major version exactly, and Edge updates itself in the
background, so a working pairing silently breaks weeks later. Chrome already
holds roughly 69% of Windows traffic, so targeting it covers the large
majority of machines with one code path and no version landmine.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# macOS installs the app bundle here by convention; a per-user install lands
# under ~/Applications. Both are checked because machines managed by MDM or
# hand-installed copies routinely deviate from the default.
_MACOS_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "~/Applications/Chromium.app/Contents/MacOS/Chromium",
)

# Windows keeps per-machine and per-user installs side by side; the x86 path
# is what 32-bit Chrome lands in, and LOCALAPPDATA covers the "install for
# me only" option.
_WINDOWS_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Chromium\Application\chrome.exe",
)

# Linux has no canonical install path, so resolve through PATH instead.
_LINUX_CHROME_COMMANDS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)

_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)\.\d+")
_DRIVER_VERSION_TIMEOUT_SECONDS = 20
_CHROME_VERSION_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class BrowserInstallation:
    """A browser we can drive, plus the major version its driver must match."""

    kind: str  # 'chrome' | 'chromium'
    executable: str
    major_version: int

    @property
    def label(self) -> str:
        return "Google Chrome" if self.kind == "chrome" else "Chromium"


class BrowserNotFoundError(RuntimeError):
    """No supported browser is installed on this machine."""


def find_browser() -> BrowserInstallation:
    """Return the first usable Chrome/Chromium, or raise ``BrowserNotFoundError``.

    Chrome is preferred over Chromium because the two ship different driver
    builds; mixing them is the most common cause of a driver refusing to
    attach to the browser it was downloaded for.
    """
    candidates = (
        _darwin_candidates() + _windows_candidates() + _linux_candidates()
    )
    for kind, executable in candidates:
        version = _read_major_version(executable)
        if version is not None:
            return BrowserInstallation(
                kind=kind, executable=executable, major_version=version
            )
    raise BrowserNotFoundError(
        "未检测到 Google Chrome 或 Chromium。语雀网页登录需要其中之一，"
        "请安装后重试。"
    )


def browser_installed() -> bool:
    """Cheap probe for UI decisions; never raises."""
    try:
        find_browser()
    except BrowserNotFoundError:
        return False
    except OSError:
        return False
    return True


# -- candidate discovery ----------------------------------------------------


def _darwin_candidates() -> list[tuple[str, str]]:
    if os.name != "posix" or not _is_macos():
        return []
    found: list[tuple[str, str]] = []
    for raw in _MACOS_CHROME_PATHS:
        path = Path(raw).expanduser()
        if path.is_file():
            found.append(("chromium" if "Chromium" in raw else "chrome", str(path)))
    return found


def _windows_candidates() -> list[tuple[str, str]]:
    if os.name != "nt":
        return []
    found: list[tuple[str, str]] = []
    for raw in _WINDOWS_CHROME_PATHS:
        path = Path(os.path.expandvars(raw))
        if path.is_file():
            found.append(("chromium" if "Chromium" in raw else "chrome", str(path)))
    return found


def _linux_candidates() -> list[tuple[str, str]]:
    if os.name != "posix" or _is_macos():
        return []
    found: list[tuple[str, str]] = []
    for command in _LINUX_CHROME_COMMANDS:
        resolved = shutil.which(command)
        if resolved:
            found.append(("chromium" if "chromium" in command else "chrome", resolved))
    return found


def _is_macos() -> bool:
    return sys.platform == "darwin"


# -- version reading --------------------------------------------------------


def _read_major_version(executable: str) -> int | None:
    """Run ``<browser> --version`` and pull the major number out.

    A browser that is installed but cannot report a version (a broken shim, a
    binary that hangs waiting for a display) is treated as absent: better to
    keep looking than to pair it with a driver we cannot trust.
    """
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=_CHROME_VERSION_TIMEOUT_SECONDS,
            # A non-zero exit is not an error here: the version string is the
            # payload, and some builds print it to stderr. Only a missing
            # binary or a timeout means "unusable".
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_PATTERN.search(result.stdout or "")
    return int(match.group(1)) if match else None


def driver_major_version(driver_path: Path) -> int | None:
    """Read the major version from a downloaded chromedriver binary."""
    try:
        result = subprocess.run(
            [str(driver_path), "--version"],
            capture_output=True,
            text=True,
            timeout=_DRIVER_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_PATTERN.search(result.stdout or "")
    return int(match.group(1)) if match else None
