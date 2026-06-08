import logging
import time as _time
from urllib.parse import urlsplit

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from base.android import handle_biometric_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.authbound.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.authbound.pages.pin_page import PinPage, HEADING as _pin_heading
from wallets.authbound.pages.error_page import ErrorPage, SCREEN_ID as _error_id
from wallets.authbound.pages.verification_request_page import (
    VerificationRequestPage,
    SCREEN_ID as _request_id,
    NO_MATCHING_CREDENTIALS_ID as _no_credentials_id,
)

logger = logging.getLogger(__name__)

# The openid4vp:// scheme is registered by many wallets on the device, so firing a
# verifier deeplink can raise Android's app-chooser (ResolverActivity). Select Authbound
# (and "Just once") to route the request into this wallet.
_CHOOSER_ITEM = (AppiumBy.XPATH, '//*[@text="Authbound Wallet"]')
_CHOOSER_ONCE = (AppiumBy.XPATH, '//*[@text="Just once"]')


def _native_deeplink(url: str) -> str:
    """Rebuild a Paradym https invitation under authbound's own scheme.

    Paradym serves an `https://paradym.id/invitation?...` wrapper that is NOT routable to
    authbound (the app only verifies app-links for `app.authbound.io`, not `paradym.id`), so
    `mobile: deepLink` would fail. The invitation already carries the real request in its query
    (`request_uri=...&client_id=...`), so rebuild it as the `openid4vp://` scheme authbound
    handles, preserving the full query string (dropping `client_id` causes a MissingClientId
    error). Non-paradym URLs (already a wallet scheme) pass through unchanged.
    """
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and "paradym.id" in parts.netloc and parts.query:
        logger.info("[verification_flow] Rebuilding paradym invitation as openid4vp://")
        return f"openid4vp://?{parts.query}"
    return url


def _wait_for_request(driver, pin: str, page_args: dict, timeout: float) -> str:
    """Poll until the presentation-request, error, or home screen appears.

    Handles interstitials inline (any order):
      - Android's app-chooser (multiple wallets share the openid4vp scheme),
      - the Android biometric (fingerprint) prompt,
      - the app passcode screen (if the wallet re-locks on resume) — entered with `pin`.

    Returns 'request', 'error', 'home', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if wait_present(driver, _CHOOSER_ITEM, timeout=1):
                logger.info("[verification_flow] App chooser — selecting Authbound Wallet")
                driver.find_element(*_CHOOSER_ITEM).click()
                if wait_present(driver, _CHOOSER_ONCE, timeout=2):
                    driver.find_element(*_CHOOSER_ONCE).click()
                continue
            if handle_biometric_if_present(driver):
                logger.info("[verification_flow] Biometric prompt — fingerprint injected")
                continue
            if wait_present(driver, _pin_heading, timeout=1):
                logger.info("[verification_flow] Passcode screen — entering PIN")
                PinPage(driver, **page_args).enter_pin(pin)
                continue
            if wait_present(driver, _error_id, timeout=1):
                return "error"
            if wait_present(driver, _request_id, timeout=1):
                return "request"
            if wait_present(driver, _home_id, timeout=1):
                return "home"
        except WebDriverException as exc:
            raise RuntimeError(
                f"[verification_flow] Appium/UiAutomator2 connection lost mid-wait "
                f"(app may have crashed): {exc.msg}"
            ) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the authbound (EUDI) wallet.

    NOTE on backgrounding: like issuance, authbound processes the deeplink via onNewIntent only
    when it is already in the foreground — pressing HOME first only resumes the task without
    delivering the request, so we fire the deeplink directly.

    Known limitation: verification is gated behind a valid authenticated profile (same gate as
    issuance) and the wallet is currently empty, so the request/share screens cannot be observed
    yet — this flow detects the generic error screen and the "no matching credentials" state and
    reports them clearly. The 'request' (share) path is scaffolded but its locators in
    verification_request_page.py are placeholders until the happy path can be observed on an
    authenticated wallet holding a matching credential.
    """
    url = _native_deeplink(provider.get(credential_name))

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    except WebDriverException as exc:
        raise RuntimeError(
            f"[verification_flow] Could not open deeplink for '{credential_name}' — "
            f"URL not routable to {app_package} ({url[:60]}…): {exc.msg}"
        ) from exc
    _time.sleep(3)

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    result = _wait_for_request(driver, pin, page_args, timeout=t)
    logger.info(f"[verification_flow] Result (waited up to {t}s): {result}")

    if result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[verification_flow] Error screen: {error_text}")
        # Leave the error screen up so teardown's failure-artifact capture dumps it.
        raise RuntimeError(
            f"[verification_flow] Verification failed for '{credential_name}': {error_text}"
        )

    if result == "home":
        raise RuntimeError(
            f"[verification_flow] App returned to home without showing a request screen for "
            f"'{credential_name}' — the deeplink was not processed or the verifier rejected silently"
        )

    if result == "timeout":
        raise RuntimeError(
            f"[verification_flow] Presentation request screen did not appear "
            f"after deeplink for '{credential_name}' (waited {t}s)"
        )

    # result == "request"
    if wait_present(driver, _no_credentials_id, timeout=2):
        raise RuntimeError(
            f"[verification_flow] No matching credentials for '{credential_name}' — "
            "wallet has no credential to share with this verifier"
        )

    logger.info("[verification_flow] Presentation request screen — sharing credentials")
    VerificationRequestPage(driver, **page_args).share()

    # A post-share error dialog may appear (e.g. protocol/cert failure) — without this check
    # the test would pass silently despite the failure.
    if wait_present(driver, _error_id, timeout=5):
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[verification_flow] Error screen after sharing: {error_text}")
        raise RuntimeError(
            f"[verification_flow] Verification failed after sharing '{credential_name}': {error_text}"
        )

    logger.info("[verification_flow] Waiting for home screen after sharing")
    HomePage(driver, **page_args).wait_until_loaded()
    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
