import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.procivis.pages import pin_page as pin_screen
from wallets.procivis.pages.pin_page import PinPage, biometric_on_screen
from wallets.procivis.pages.proof_request_sharing_page import (
    ProofRequestSharingPage,
    on_screen as sharing_on_screen,
    SCREEN_ID as _sharing_id,
)
from wallets.procivis.pages.proof_request_accept_process_page import (
    ProofRequestAcceptProcessPage,
    SCREEN_ID as _process_id,
)

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Procivis wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package (e.g. "ch.procivis.one.wallet.trial")
        pin:             PIN to unlock the app — required if the app is locked on deeplink
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    url = provider.get(credential_name)
    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    if biometric_on_screen(driver):
        if not pin:
            raise RuntimeError(
                "App showed biometric prompt after deeplink — pass pin= to verification_flow.run()"
            )
        logger.info("[verification_flow] Biometric prompt — falling back to PIN")
        PinPage(driver, **page_args).dismiss_biometric_prompt()

    if pin_screen.on_screen(driver):
        if not pin:
            raise RuntimeError(
                "App showed PIN screen after deeplink — pass pin= to verification_flow.run()"
            )
        logger.info("[verification_flow] PIN screen appeared — unlocking")
        PinPage(driver, **page_args).enter_login_pin(pin)

    if not sharing_on_screen(driver, timeout=15):
        try:
            elements = driver.find_elements(AppiumBy.XPATH, '//*[@text!=""]')
            visible = [el.get_attribute("text") for el in elements if el.get_attribute("text")]
        except Exception:
            visible = []
        screen_info = f" — visible text: {visible}" if visible else ""
        raise RuntimeError(
            f"[verification_flow] ProofRequestSharingScreen did not appear after deeplink "
            f"for '{credential_name}'{screen_info}"
        )

    logger.info("[verification_flow] Sharing screen — sharing credentials")
    ProofRequestSharingPage(driver, **page_args).share()

    if wait_present(driver, _process_id, timeout=5):
        logger.info("[verification_flow] Accept process screen — waiting for completion")
        ProofRequestAcceptProcessPage(driver, **page_args).wait_for_result()

    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
