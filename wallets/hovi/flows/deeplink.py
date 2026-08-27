"""Explain, after the fact, why a deeplink never reached hovi.

`mobile: deepLink` does not fail when the wallet cannot handle a URL. The intent goes to whatever
else claims it (a browser, the launcher), hovi never comes forward, and the flow times out, which
looks exactly like a crashed app or a dead session. Once a case has failed, this asks the device
which schemes hovi registers and reports `unroutable` when that explains it.

Diagnosis only, never a gate: every URL is fired exactly as the provider published it. Reading the
device keeps the evidence tied to the build under test, and each run records it in `app_info.json`
(see `base/utils.get_app_info`).
"""
import logging
from urllib.parse import urlsplit

from base.utils import get_app_info

logger = logging.getLogger(__name__)

# Read once per process. Schemes change only when the app is replaced.
_scheme_cache: dict[str, list[str]] = {}


def _device_serial(driver) -> str:
    """ADB serial for adb-based helpers; empty string targets the only device."""
    try:
        caps = driver.capabilities or {}
        return caps.get("udid") or caps.get("deviceName") or ""
    except Exception:
        return ""


def registered_schemes(driver, app_package: str) -> list[str]:
    """URL schemes hovi declares an intent filter for, straight from the device.

    Returns an empty list when the dump could not be read, which callers must treat as "don't
    know", never as "the wallet registers nothing".
    """
    if app_package not in _scheme_cache:
        schemes = get_app_info(app_package, _device_serial(driver)).get("schemes", [])
        _scheme_cache[app_package] = schemes
        logger.info(f"[deeplink] {app_package} registers: {', '.join(schemes) or '(unreadable)'}")
    return _scheme_cache[app_package]


def unroutable_reason(driver, app_package: str, url: str) -> str:
    """Why `url` could not reach hovi, or "" if its scheme is registered (or unknown).

    Tells "this URL was never deliverable here" apart from "the wallet did not come forward".
    Returns "" on an unreadable dump: an unproven accusation about a provider is worse than a
    vaguer failure.
    """
    schemes = registered_schemes(driver, app_package)
    if not schemes:
        return ""

    scheme = urlsplit(url).scheme.lower()
    if scheme in schemes:
        return ""

    return (
        f"the provider returned a {scheme + '://' if scheme else 'scheme-less'} URL "
        f"({url[:70]}…), which hovi does not register. It claims only "
        f"{', '.join(schemes)}. The deeplink was delivered to whatever else on the device "
        "claims that scheme, so it could never reach this wallet"
    )
