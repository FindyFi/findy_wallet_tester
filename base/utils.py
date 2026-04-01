import subprocess
from pathlib import Path
from typing import Tuple

import requests as _requests

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


def list_wallets() -> list[str]:
    """Return sorted wallet directory names, excluding private dirs (prefixed with _)."""
    wallets_dir = Path(__file__).parent.parent / "wallets"
    return sorted(
        d.name for d in wallets_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )


def get_app_info(package_name: str, device_serial: str = "") -> dict:
    """Return version info for an installed Android package.

    Queries the device via ``adb shell dumpsys package`` and parses
    ``versionName`` and ``versionCode`` from the output.

    Args:
        package_name:   Android package identifier (e.g. "id.paradym.wallet")
        device_serial:  ADB device serial (e.g. "emulator-5554").
                        When empty, adb targets the only connected device.

    Returns a dict with keys: package, version_name, version_code.
    All values fall back to "unknown" on any adb failure.
    """
    cmd = ["adb"]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["shell", "dumpsys", "package", package_name]

    version_name = "unknown"
    version_code = "unknown"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("versionName="):
                version_name = line.split("=", 1)[1]
            elif "versionCode=" in line and version_code == "unknown":
                for part in line.split():
                    if part.startswith("versionCode="):
                        version_code = part.split("=", 1)[1]
                        break
    except Exception:
        pass

    return {
        "package": package_name,
        "version_name": version_name,
        "version_code": version_code,
    }


def check_provider_reachable(base_url: str, timeout: float = 10) -> Tuple[bool, str]:
    """Return (True, "") if base_url responds with a non-5xx status, else (False, reason)."""
    try:
        resp = _requests.get(base_url.rstrip("/") + "/", timeout=timeout, allow_redirects=True)
        if resp.status_code < 500:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except _requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {e}"
    except _requests.exceptions.Timeout:
        return False, f"Timeout after {timeout}s"
    except Exception as e:
        return False, str(e)


def sanitize_test_name(name: str) -> str:
    """Return a filesystem-safe version of a pytest test name.

    Replaces characters that are invalid or awkward in filenames:
    '[' and ']' (from parametrize) and '/' (from nested IDs).
    """
    return name.replace("[", "_").replace("]", "").replace("/", "_")


def wait_present(driver, locator, timeout: float = 2) -> bool:
    """Return True if the element appears within timeout seconds."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(locator))
        return True
    except TimeoutException:
        return False
