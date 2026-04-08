import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from wallets.procivis.pages import pin_page as pin_screen
from wallets.procivis.pages.pin_page import PinPage, biometric_on_screen
from wallets.procivis.pages.credential_offer_page import CredentialOfferPage, SCREEN_ID as _offer_id
from wallets.procivis.pages.credential_accept_process_page import CredentialAcceptProcessPage, SCREEN_ID as _process_id
from base.utils import wait_present
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Procivis wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pid_issuance")
        app_package:     App package (e.g. "ch.procivis.one.wallet.trial")
        pin:             PIN to unlock the app — always required as the app locks on deeplink
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    url = provider.get(credential_name)
    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    if biometric_on_screen(driver):
        if not pin:
            raise RuntimeError(
                "App showed biometric prompt after deeplink — pass pin= to credential_flow.run()"
            )
        logger.info("[credential_flow] Biometric prompt — falling back to PIN")
        PinPage(driver, **page_args).dismiss_biometric_prompt()

    if pin_screen.on_screen(driver):
        if not pin:
            raise RuntimeError(
                "App showed PIN screen after deeplink — pass pin= to credential_flow.run()"
            )
        logger.info("[credential_flow] PIN screen appeared — unlocking")
        PinPage(driver, **page_args).enter_login_pin(pin)

    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    try:
        WebDriverWait(driver, t).until(
            EC.any_of(
                EC.presence_of_element_located(_offer_id),
                EC.presence_of_element_located(_process_id),
            )
        )
    except TimeoutException:
        raise RuntimeError(
            f"[credential_flow] CredentialOfferScreen did not appear after deeplink "
            f"for '{credential_name}'"
        )

    if wait_present(driver, _process_id, timeout=1):
        logger.info("[credential_flow] App went directly to error screen — collecting details")
        CredentialAcceptProcessPage(driver, **page_args).wait_for_result()  # raises with details

    logger.info("[credential_flow] Credential offer screen — accepting")
    CredentialOfferPage(driver, **page_args).accept()

    # Wait for CredentialOfferScreen to disappear — this always happens on success.
    # CredentialAcceptProcessScreen may or may not appear depending on app version.
    try:
        WebDriverWait(driver, t).until(EC.invisibility_of_element_located(_offer_id))
    except TimeoutException:
        raise RuntimeError(
            f"[credential_flow] CredentialOfferScreen did not dismiss after Accept for '{credential_name}'"
        )

    # If the processing screen appears, let it complete (handles error detection too).
    if wait_present(driver, _process_id, timeout=2):
        logger.info("[credential_flow] Processing screen appeared — waiting for completion")
        CredentialAcceptProcessPage(driver, **page_args).wait_for_result()

    logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
