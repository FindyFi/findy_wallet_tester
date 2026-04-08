import logging
import time as _time

from base.android import handle_biometric_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.heidi.pages.home_page import SCREEN_ID as _home_id
from wallets.heidi.pages.untrusted_connection_page import (
    UntrustedConnectionPage,
    SCREEN_ID as _untrusted_id,
    on_screen as untrusted_on_screen,
)
from wallets.heidi.pages.error_page import ErrorPage, SCREEN_ID as _error_id
from wallets.heidi.pages.verification_request_page import (
    VerificationRequestPage,
    on_screen as request_on_screen,
)

logger = logging.getLogger(__name__)

# Android HOME key — used to background Heidi before firing the deeplink.
# Heidi requires the app to be in the background to process incoming deeplinks correctly.
_KEYCODE_HOME = 3


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

    # Biometric may appear first, then the Untrusted Connection screen after.
    # After biometric, Heidi briefly flashes home before navigating to the
    # request screen — wait for that transition to complete.
    if handle_biometric_if_present(driver):
        _time.sleep(10)

    # Some verifiers are not in Heidi's trust list, triggering an "Untrusted Connection" prompt.
    if untrusted_on_screen(driver, timeout=5):
        logger.info("[verification_flow] Untrusted Connection screen — accepting")
        UntrustedConnectionPage(driver, **page_args).connect_anyway()

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    if not request_on_screen(driver, timeout=t):
        if wait_present(driver, _error_id, timeout=2):
            error_page = ErrorPage(driver, **page_args)
            error_text = error_page.get_error_text()
            logger.error(f"[verification_flow] Error screen: {error_text}")
            error_page.cancel()
            raise RuntimeError(
                f"[verification_flow] Verification failed for '{credential_name}': {error_text}"
            )
        raise RuntimeError(
            f"[verification_flow] Information Request screen did not appear "
            f"after deeplink for '{credential_name}' (waited {t}s)"
        )

    logger.info("[verification_flow] Information Request screen — sharing credentials")
    VerificationRequestPage(driver, **page_args).share()

    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
