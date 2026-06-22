import logging
import time
from urllib.parse import urlparse

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from appium.webdriver.common.appiumby import AppiumBy

from providers.base import DeeplinkProvider
from base.android import authenticate_with_pin, BIOMETRIC_PROMPT
from base.utils import wait_present
from wallets.gataca.pages.home_page import SCREEN_ID as _home_id
from wallets.gataca.pages.credential_offer_page import CredentialOfferPage, on_screen as _offer_on_screen
from wallets.gataca.pages.error_page import ErrorPage, on_screen as _error_on_screen
from wallets.gataca.pages.success_page import SuccessPage, on_screen as _success_on_screen

_REJECTED_SCREEN = (AppiumBy.XPATH, '//*[@text="Rejected"]')
_REJECTED_OK = (AppiumBy.XPATH, '//*[@text="OK" and @clickable="true"]')

logger = logging.getLogger(__name__)


def _wait_for_result(driver, offer_timeout: float, device_pin: str):
    """Poll until the presentation request, error, or home screen appears.

    Some verifiers raise a system biometric prompt *before* showing the request screen. That
    prompt is not request/error/home, so without handling it the poll would just time out while
    the wallet is actually waiting on us. Authenticate it via PIN as soon as it appears, then
    keep polling for the real result.
    """
    deadline = time.time() + offer_timeout
    while time.time() < deadline:
        if wait_present(driver, BIOMETRIC_PROMPT, timeout=1):
            logger.info("[verification_flow] Biometric prompt before request — authenticating with PIN")
            authenticate_with_pin(driver, device_pin)
            continue
        if _offer_on_screen(driver, timeout=1):
            return "request"
        if _error_on_screen(driver, timeout=1):
            return "error"
        if wait_present(driver, _home_id, timeout=1):
            return "home"
        time.sleep(0.5)
    return "timeout"


def _handle_biometric_and_confirm(driver, offer_timeout: float, **page_args):
    """After tapping Share: authenticate the system biometric prompt via PIN,
    then confirm the Login Successful dialog."""
    device_pin = page_args.get("device_pin", "")
    time.sleep(2)
    logger.info("[verification_flow] Authenticating biometric prompt with PIN")
    authenticate_with_pin(driver, device_pin)

    deadline = time.time() + offer_timeout
    while time.time() < deadline:
        if _success_on_screen(driver, timeout=1):
            logger.info("[verification_flow] Login Successful — tapping OK")
            SuccessPage(driver, **page_args).confirm()
            return
        if wait_present(driver, _home_id, timeout=1):
            return
        if wait_present(driver, _REJECTED_SCREEN, timeout=1):
            logger.warning("[verification_flow] Verification rejected by backend — tapping OK")
            try:
                driver.find_element(*_REJECTED_OK).click()
            except Exception:
                pass
            raise RuntimeError("[verification_flow] Credential verification was rejected (wrong DID method?)")
        if _error_on_screen(driver, timeout=1):
            raise RuntimeError("[verification_flow] Error screen after biometric")
    raise RuntimeError(f"[verification_flow] No success/home screen after biometric within {offer_timeout}s")


def _to_openid4vp(url: str) -> str:
    """Convert an https:// invitation URL to the openid4vp:// scheme Gataca handles.

    Paradym verifiers return https://paradym.id/invitation?request_uri=...
    Gataca registers for openid4vp:// but not for paradym.id App Links.
    """
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.query:
        return f"openid4vp://?{parsed.query}"
    return url


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Gataca wallet.

    Flow: deeplink → Sharing requirements → Share → biometric → Login Successful → OK → home
    """
    timeouts = page_args.get("timeouts", {})
    offer_timeout = timeouts.get("credential_offer", 30)
    device_pin = page_args.get("device_pin", "") or pin

    url = provider.get(credential_name)
    url = _to_openid4vp(url)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    result = _wait_for_result(driver, offer_timeout, device_pin)

    if result == "request":
        logger.info("[verification_flow] Sharing requirements — tapping Share")
        CredentialOfferPage(driver, **page_args).accept()
        _handle_biometric_and_confirm(driver, offer_timeout, **page_args)
        try:
            WebDriverWait(driver, offer_timeout).until(
                EC.presence_of_element_located(_home_id)
            )
        except TimeoutException:
            raise RuntimeError("[verification_flow] Did not return to home after sharing")
        logger.info("[verification_flow] Verification complete")

    elif result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.warning(f"[verification_flow] Error screen: {error_text}")
        ErrorPage(driver, **page_args).go_back()
        raise RuntimeError(f"[verification_flow] Verification failed: {error_text}")

    elif result == "home":
        logger.info("[verification_flow] App returned to home without showing request screen")

    else:
        raise RuntimeError(
            f"[verification_flow] No request/error/home screen after {offer_timeout}s for '{credential_name}'"
        )
