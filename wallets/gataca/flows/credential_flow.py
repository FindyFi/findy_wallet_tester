import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from providers.base import DeeplinkProvider
from base.android import authenticate_with_pin, BIOMETRIC_PROMPT
from base.utils import wait_present
from wallets.gataca.pages.home_page import SCREEN_ID as _home_id

# "Email: Unavailable" indicator — shown when the active DID has no email credential.
_EMAIL_UNAVAILABLE = (AppiumBy.XPATH, '//*[@text="Unavailable"]')
_GET_CREDENTIALS_BTN = (AppiumBy.XPATH, '//*[@content-desc="Get Credentials"]')
from wallets.gataca.pages.credential_offer_page import CredentialOfferPage, on_screen as _offer_on_screen
from wallets.gataca.pages.error_page import ErrorPage, on_screen as _error_on_screen
from wallets.gataca.pages.success_page import SuccessPage, on_screen as _success_on_screen

logger = logging.getLogger(__name__)


def _wait_for_result(driver, offer_timeout: float, device_pin: str):
    """Poll until a credential offer, success, error, or home screen appears.

    Some issuers (e.g. procivis, authorization_code grant) raise a system biometric prompt
    *before* showing any offer screen, and then issue the credential directly — landing on the
    "Credentials Shared" success screen without an offer to accept. So we authenticate the prompt
    via PIN as soon as it appears, and treat the success screen as a terminal result, not just the
    offer screen.
    """
    deadline = time.time() + offer_timeout
    while time.time() < deadline:
        if wait_present(driver, BIOMETRIC_PROMPT, timeout=1):
            logger.info("[credential_flow] Biometric prompt before offer — authenticating with PIN")
            authenticate_with_pin(driver, device_pin)
            continue
        if _success_on_screen(driver, timeout=1):
            return "success"
        if _offer_on_screen(driver, timeout=1):
            return "offer"
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
    logger.info("[credential_flow] Authenticating biometric prompt with PIN")
    authenticate_with_pin(driver, device_pin)

    # Wait for Login Successful dialog or home
    deadline = time.time() + offer_timeout
    while time.time() < deadline:
        if _success_on_screen(driver, timeout=1):
            logger.info("[credential_flow] Login Successful — tapping OK")
            SuccessPage(driver, **page_args).confirm()
            # Pension flow uses authorization_code grant: after OIDC auth completes,
            # the wallet pops a SECOND biometric prompt to sign the credential request.
            time.sleep(2)
            logger.info("[credential_flow] Authenticating post-auth issuance prompt with PIN")
            authenticate_with_pin(driver, device_pin)
            return
        if wait_present(driver, _home_id, timeout=1):
            return
        if _error_on_screen(driver, timeout=1):
            raise RuntimeError("[credential_flow] Error screen after biometric")
    raise RuntimeError(f"[credential_flow] No success/home screen after biometric within {offer_timeout}s")


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Gataca wallet.

    Flow: deeplink → Sharing requirements → Share → biometric → Login Successful → OK → home
    """
    timeouts = page_args.get("timeouts", {})
    offer_timeout = timeouts.get("credential_offer", 30)
    device_pin = page_args.get("device_pin", "") or pin

    url = provider.get(credential_name)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    result = _wait_for_result(driver, offer_timeout, device_pin)

    if result == "success":
        # authorization_code issuers (e.g. procivis) issue directly after the pre-offer biometric,
        # landing on "Credentials Shared" with no offer to accept. Confirm it and we're done.
        logger.info("[credential_flow] Credential issued (Credentials Shared) — tapping OK")
        SuccessPage(driver, **page_args).confirm()
        logger.info("[credential_flow] Credential flow complete")

    elif result == "offer":
        if wait_present(driver, _EMAIL_UNAVAILABLE, timeout=2):
            logger.warning("[credential_flow] Email credential unavailable for active DID")
            try:
                driver.find_element(*_GET_CREDENTIALS_BTN)
                driver.back()
            except Exception:
                pass
            raise RuntimeError(
                "[credential_flow] Email credential not available for the active DID. "
                "Complete the one-time email verification flow manually first: "
                "open the pension issuer → tap 'Get Credentials' → enter email → biometric → OTP."
            )
        logger.info("[credential_flow] Sharing requirements — tapping Share")
        CredentialOfferPage(driver, **page_args).accept()
        _handle_biometric_and_confirm(driver, offer_timeout, **page_args)
        try:
            WebDriverWait(driver, offer_timeout).until(
                EC.presence_of_element_located(_home_id)
            )
        except TimeoutException:
            raise RuntimeError("[credential_flow] Did not return to home after accepting offer")
        logger.info("[credential_flow] Credential flow complete")

    elif result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.warning(f"[credential_flow] Error screen: {error_text}")
        ErrorPage(driver, **page_args).go_back()
        raise RuntimeError(f"[credential_flow] Credential offer failed: {error_text}")

    elif result == "home":
        logger.info("[credential_flow] App returned to home without showing offer screen")

    else:
        raise RuntimeError(
            f"[credential_flow] No offer/error/home screen after {offer_timeout}s for '{credential_name}'"
        )
