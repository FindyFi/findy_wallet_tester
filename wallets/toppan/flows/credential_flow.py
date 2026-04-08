import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from base.android import handle_permission_if_present
from base.utils import wait_present
from wallets.toppan.flows import check_for_error, PROCESSING
from wallets.toppan.pages.credential_offer_page import (
    ConfirmationPage, confirmation_on_screen,
    CredentialOfferPage,
)
from wallets.toppan.pages.home_page import SCREEN_ID as _home_id
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Toppan wallet.

    Flow:
      1. Fire deeplink
      2. "Do you want to proceed with OpenID4VCI issuance?" dialog → CONTINUE
      3. "OID4VCI Issuance" credential offer modal → Accept
      4. Wait for "Adding your credential" processing overlay to clear
      5. Verify home screen (or raise on error dialog)
    """
    timeouts = page_args.get("timeouts", {})
    credential_offer_timeout = timeouts.get("credential_offer", 30)
    default_timeout = timeouts.get("default", 10)

    url = provider.get(credential_name)
    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    handle_permission_if_present(driver)
    check_for_error(driver, "after deeplink")

    if confirmation_on_screen(driver, timeout=credential_offer_timeout):
        logger.info("[credential_flow] Confirmation dialog — tapping CONTINUE")
        ConfirmationPage(driver, **page_args).confirm()
        check_for_error(driver, "after confirmation")

    logger.info("[credential_flow] Waiting for credential offer screen")
    CredentialOfferPage(driver, **page_args).accept()
    logger.info("[credential_flow] Tapped Accept — waiting for processing to complete")

    # Wait for the processing overlay to disappear, then check for errors.
    if wait_present(driver, PROCESSING, timeout=5):
        try:
            WebDriverWait(driver, credential_offer_timeout).until(
                lambda d: not wait_present(d, PROCESSING, timeout=0.5)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Credential processing timed out after {credential_offer_timeout}s"
            )

    check_for_error(driver, "after processing")

    if wait_present(driver, _home_id, timeout=default_timeout):
        logger.info(f"[credential_flow] Credential '{credential_name}' added — home screen reached")
    else:
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
