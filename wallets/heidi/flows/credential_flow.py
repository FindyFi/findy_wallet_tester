import logging
import time as _time

from selenium.common.exceptions import WebDriverException

from base.android import handle_biometric_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.heidi.pages.home_page import SCREEN_ID as _home_id
from wallets.heidi.pages.connection_page import (
    ConnectionPage,
    SCREEN_ID as _connection_id,
)
from wallets.heidi.pages.error_page import ErrorPage, SCREEN_ID as _error_id
from wallets.heidi.pages.credential_offer_page import (
    CredentialOfferPage,
    SCREEN_ID as _offer_id,
)

logger = logging.getLogger(__name__)

# Android HOME key — used to background Heidi before firing the deeplink.
# Heidi requires the app to be in the background to process incoming deeplinks correctly.
_KEYCODE_HOME = 3


def _wait_for_result(driver, error_id, offer_id, home_id, connection_id, timeout: float):
    """Poll until the error, offer, or home screen appears.

    Two interstitial screens can appear mid-wait, in either order and more than
    once, so both are handled inside the loop rather than once up front:
      - the Android biometric (fingerprint) prompt — Heidi raises it *after* the
        connection screen is accepted, so it must be injected here; otherwise the
        wait times out on the fingerprint screen and never reaches the
        error/offer screen.
      - the connection-consent screen — trusted ("CONNECT") or untrusted
        ("CONNECT ANYWAY"); ConnectionPage accepts whichever variant appears.

    Returns 'error', 'offer', 'home' (issuer rejected silently), or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[credential_flow] Biometric prompt — fingerprint injected")
                continue
            if wait_present(driver, connection_id, timeout=1):
                logger.info("[credential_flow] Connection consent screen — accepting")
                ConnectionPage(driver).connect()
                continue
            if wait_present(driver, error_id, timeout=1):
                return "error"
            if wait_present(driver, offer_id, timeout=1):
                return "offer"
            if wait_present(driver, home_id, timeout=1):
                return "home"
        except WebDriverException as exc:
            raise RuntimeError(
                f"[credential_flow] Appium/UiAutomator2 connection lost mid-wait "
                f"(app may have crashed): {exc.msg}"
            ) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Heidi wallet.

    Handles:
      - "Untrusted Connection" consent screen
      - Android biometric (fingerprint) prompt
      - "Add Credential" offer screen — accepts the credential
      - Generic error screen ("An error occurred.") — logs the error and raises RuntimeError
      - Silent rejection — app returns to home without showing any UI
    """
    url = provider.get(credential_name)

    # Heidi only processes incoming deeplinks when the app is in the background.
    # Press HOME to background the app before firing the deeplink.
    logger.info("[credential_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)
    _time.sleep(10)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    _time.sleep(10)

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 60))

    # _wait_for_result handles the biometric (fingerprint) prompt and the
    # connection-consent screen (trusted or untrusted) internally, so they are
    # caught regardless of the order in which Heidi presents them.
    result = _wait_for_result(
        driver, _error_id, offer_id=_offer_id, home_id=_home_id,
        connection_id=_connection_id, timeout=t,
    )
    logger.info(f"[credential_flow] Result after {t}s wait: {result}")

    if result == "error":
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[credential_flow] Error screen: {error_text}")
        # Leave the error screen on display so the failure-artifact capture in
        # teardown dumps the error screen (not the screen after a CANCEL tap).
        # Teardown's init_flow navigates back to home afterwards, dismissing it.
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
            f"'{credential_name}' — issuer may have rejected silently or the deeplink was not processed"
        )

    raise RuntimeError(
        f"[credential_flow] No recognisable screen appeared after deeplink "
        f"for '{credential_name}' (timed out after {t}s)"
    )
