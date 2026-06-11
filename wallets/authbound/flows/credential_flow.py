import logging
import time as _time
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException

from base.android import handle_biometric_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.authbound.pages.home_page import SCREEN_ID as _home_id
from wallets.authbound.pages.pin_page import PinPage, HEADING as _pin_heading
from wallets.authbound.pages.error_page import ErrorPage, SCREEN_ID as _error_id
from wallets.authbound.pages.credential_offer_page import (
    CredentialOfferPage,
    SCREEN_ID as _offer_id,
)

logger = logging.getLogger(__name__)


def _native_deeplink(url: str) -> str:
    """Rebuild a Paradym https invitation under authbound's own scheme.

    Paradym serves an `https://paradym.id/invitation?...` wrapper that is NOT routable to
    authbound (the app only verifies app-links for `app.authbound.io`, not `paradym.id`), so
    `mobile: deepLink` would fail. The invitation already carries the real endpoint in its query
    (`credential_offer_uri=...`), so rebuild it as the `openid-credential-offer://` scheme that
    authbound exclusively handles, preserving the full query string. Non-paradym URLs (already a
    wallet scheme) pass through unchanged.
    """
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and "paradym.id" in parts.netloc and parts.query:
        logger.info("[credential_flow] Rebuilding paradym invitation as openid-credential-offer://")
        return f"openid-credential-offer://?{parts.query}"
    return url


def _wait_for_result(driver, pin: str, page_args: dict, timeout: float) -> str:
    """Poll until the error, offer, or home screen appears.

    Handles two interstitials inline (they can appear in any order):
      - the Android biometric (fingerprint) prompt,
      - the app passcode screen (if the wallet re-locks on resume) — entered with `pin`.

    Returns 'error', 'offer', 'home', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[credential_flow] Biometric prompt — fingerprint injected")
                continue
            if wait_present(driver, _pin_heading, timeout=1):
                logger.info("[credential_flow] Passcode screen — entering PIN")
                PinPage(driver, **page_args).enter_pin(pin)
                continue
            if wait_present(driver, _error_id, timeout=1):
                return "error"
            if wait_present(driver, _offer_id, timeout=1):
                return "offer"
            if wait_present(driver, _home_id, timeout=1):
                return "home"
        except WebDriverException as exc:
            raise RuntimeError(
                f"[credential_flow] Appium/UiAutomator2 connection lost mid-wait "
                f"(app may have crashed): {exc.msg}"
            ) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the authbound (EUDI) wallet.

    NOTE on backgrounding: unlike some wallets, authbound processes the offer via
    onNewIntent only when it is already in the foreground. Pressing HOME first makes the
    deeplink merely resume the existing task ("brought to the front") WITHOUT delivering
    the offer, so we fire the deeplink directly while the app is open.

    Known limitation: the wallet gates credential issuance behind a valid authenticated
    profile. Without it, the offer is rejected before any consent/offer screen renders and
    the generic error screen appears — this flow captures that error text and raises a
    clear RuntimeError. The 'offer' (accept) path is scaffolded but its locators in
    credential_offer_page.py are still placeholders until the happy path can be observed
    on an authenticated wallet.
    """
    url = _native_deeplink(provider.get(credential_name))

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    except WebDriverException as exc:
        raise RuntimeError(
            f"[credential_flow] Could not open deeplink for '{credential_name}' — "
            f"URL not routable to {app_package} ({url[:60]}…): {exc.msg}"
        ) from exc
    _time.sleep(3)

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    result = _wait_for_result(driver, pin, page_args, timeout=t)
    logger.info(f"[credential_flow] Result (waited up to {t}s): {result}")

    if result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[credential_flow] Error screen: {error_text}")
        # Leave the error screen up so teardown's failure-artifact capture dumps it;
        # init_flow navigates home afterwards.
        raise RuntimeError(
            f"[credential_flow] Credential issuance failed for '{credential_name}': {error_text}"
        )

    if result == "offer":
        logger.info("[credential_flow] Credential offer screen — accepting")
        CredentialOfferPage(driver, **page_args).accept()
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
        return

    if result == "home":
        raise RuntimeError(
            f"[credential_flow] App returned to home without showing an offer screen for "
            f"'{credential_name}' — the deeplink was not processed or the issuer rejected silently"
        )

    raise RuntimeError(
        f"[credential_flow] No recognisable screen appeared after deeplink "
        f"for '{credential_name}' (timed out after {t}s)"
    )
