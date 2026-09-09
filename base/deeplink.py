"""Explain, after the fact, why a deeplink never reached the wallet.

`mobile: deepLink` does not fail when the wallet cannot handle a URL. The intent goes to whatever
else claims it (a browser, the launcher), the wallet never comes forward, and the flow times out,
which looks exactly like a crashed app or a dead session. Once a case has failed, this asks the
device which schemes the wallet registers and reports `unroutable` when that explains it.

Diagnosis only, never a gate: every URL is fired exactly as the provider published it. Reading the
device keeps the evidence tied to the build under test, and each run records the same schemes in
`app_info.json` (see `base/utils.get_app_info`).
"""
import logging
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from base.utils import get_app_info

logger = logging.getLogger(__name__)

# Read once per process, per package. Schemes change only when the app is replaced — which the
# suite itself can do mid-session via BaseTest.check_for_updates, so that path calls invalidate().
_scheme_cache: Dict[str, List[str]] = {}


def invalidate(app_package: Optional[str] = None) -> None:
    """Forget cached schemes, for one package or all of them.

    Called after an in-session update: the cached list would otherwise describe the build that was
    just replaced, and an `unroutable` verdict is an accusation about a provider — it must never
    rest on stale evidence.
    """
    if app_package is None:
        _scheme_cache.clear()
    else:
        _scheme_cache.pop(app_package, None)


def _device_serial(driver) -> str:
    """ADB serial for adb-based helpers; empty string targets the only device."""
    try:
        caps = driver.capabilities or {}
        return caps.get("udid") or caps.get("deviceName") or ""
    except Exception:
        return ""


def registered_schemes(driver, app_package: str) -> List[str]:
    """URL schemes the package declares an intent filter for, straight from the device.

    Returns an empty list when the dump could not be read, which callers must treat as "don't
    know", never as "the wallet registers nothing".
    """
    if app_package not in _scheme_cache:
        schemes = get_app_info(app_package, _device_serial(driver)).get("schemes", [])
        _scheme_cache[app_package] = schemes
        logger.info(f"[deeplink] {app_package} registers: {', '.join(schemes) or '(unreadable)'}")
    return _scheme_cache[app_package]


def unroutable_reason(driver, app_package: str, url: str, wallet: str = "the wallet") -> str:
    """Why `url` could not reach the wallet, or "" if its scheme is registered (or unknown).

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
        f"({url[:70]}…), which {wallet} does not register. It claims only "
        f"{', '.join(schemes)}. The deeplink was delivered to whatever else on the device "
        "claims that scheme, so it could never reach this wallet"
    )
