import logging
import time as _time

from selenium.common.exceptions import WebDriverException

from base.android import handle_biometric_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.heidi.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.heidi.pages.connection_page import (
    ConnectionPage,
    SCREEN_ID as _connection_id,
)
from wallets.heidi.pages.error_page import (
    ErrorPage,
    SCREEN_ID as _error_id,
    VERIFICATION_SCREEN_ID as _verification_error_id,
)
from wallets.heidi.pages.verification_request_page import (
    VerificationRequestPage,
    NO_MATCHING_CREDENTIALS_ID as _no_credentials_id,
    SCREEN_ID as _request_id,
)

logger = logging.getLogger(__name__)

# Android HOME key — used to background Heidi before firing the deeplink.
# Heidi requires the app to be in the background to process incoming deeplinks correctly.
_KEYCODE_HOME = 3


def _wait_for_request(driver, request_id, error_id, verification_error_id,
                      connection_id, timeout: float):
    """Poll until the Information Request screen or an error screen appears.

    Two interstitial screens can appear mid-wait, in either order and more than
    once, so both are handled inside the loop rather than once up front:
      - the Android biometric (fingerprint) prompt — Heidi raises it *after* the
        connection screen is accepted, so it must be injected here.
      - the connection-consent screen — trusted ("CONNECT") or untrusted
        ("CONNECT ANYWAY"); ConnectionPage accepts whichever variant appears.

    Returns 'request', 'error', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[verification_flow] Biometric prompt — fingerprint injected")
                continue
            if wait_present(driver, connection_id, timeout=1):
                logger.info("[verification_flow] Connection consent screen — accepting")
                ConnectionPage(driver).connect()
                continue
            if wait_present(driver, request_id, timeout=1):
                return "request"
            if (wait_present(driver, error_id, timeout=1)
                    or wait_present(driver, verification_error_id, timeout=1)):
                return "error"
        except WebDriverException as exc:
            raise RuntimeError(
                f"[verification_flow] Appium/UiAutomator2 connection lost mid-wait "
                f"(app may have crashed): {exc.msg}"
            ) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Heidi wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package (e.g. "ch.ubique.heidi.android")
        pin:             Unused — Heidi uses biometric, not PIN
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    url = provider.get(credential_name)

    # Heidi only processes incoming deeplinks when the app is in the background.
    # Press HOME to background the app before firing the deeplink.
    logger.info("[verification_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)
    _time.sleep(10)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    _time.sleep(10)

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    # _wait_for_request handles the biometric (fingerprint) prompt and the
    # connection-consent screen (trusted or untrusted) internally, so they are
    # caught regardless of the order in which Heidi presents them.
    result = _wait_for_request(
        driver, _request_id, _error_id, _verification_error_id,
        connection_id=_connection_id, timeout=t,
    )
    logger.info(f"[verification_flow] Result after {t}s wait: {result}")

    if result == "error":
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[verification_flow] Error screen: {error_text}")
        # Leave the error screen on display so the failure-artifact capture in
        # teardown dumps the error screen; teardown then navigates back to home.
        raise RuntimeError(
            f"[verification_flow] Verification failed for '{credential_name}': {error_text}"
        )

    if result == "timeout":
        raise RuntimeError(
            f"[verification_flow] Information Request screen did not appear "
            f"after deeplink for '{credential_name}' (waited {t}s)"
        )

    if wait_present(driver, _no_credentials_id, timeout=2):
        raise RuntimeError(
            f"[verification_flow] No matching credentials for '{credential_name}' — "
            "wallet has no credentials to share with this verifier"
        )

    logger.info("[verification_flow] Information Request screen — sharing credentials")
    VerificationRequestPage(driver, **page_args).share()

    # After sharing, an error dialog may appear (e.g. cert failure, protocol error).
    # Without this check the test passes silently despite the failure.
    if wait_present(driver, _error_id, timeout=5) or wait_present(driver, _verification_error_id, timeout=5):
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[verification_flow] Error screen after sharing: {error_text}")
        # Leave the error screen on display for the teardown failure-artifact dump.
        raise RuntimeError(
            f"[verification_flow] Verification failed after sharing '{credential_name}': {error_text}"
        )

    logger.info("[verification_flow] Waiting for home screen after sharing")
    HomePage(driver, **page_args).wait_until_loaded()

    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
