import logging

from base.android import handle_permission_if_present
from base.utils import wait_present
from wallets.toppan.flows import check_for_error
from wallets.toppan.pages.verification_request_page import (
    VerificationConfirmationPage, confirmation_on_screen,
    VerificationRequestPage,
)
from wallets.toppan.pages.home_page import SCREEN_ID as _home_id
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Toppan wallet.

    Flow:
      1. Fire deeplink
      2. "Do you want to proceed with verification?" dialog → CONTINUE
      3. "OID4VP Verification" request screen → Share
      4. Verify home screen (or raise on error dialog)
    """
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    url = provider.get(credential_name)
    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    handle_permission_if_present(driver)
    check_for_error(driver, "after deeplink")

    if confirmation_on_screen(driver, timeout=default_timeout):
        logger.info("[verification_flow] Confirmation dialog — tapping CONTINUE")
        VerificationConfirmationPage(driver, **page_args).confirm()
        check_for_error(driver, "after confirmation")

    logger.info("[verification_flow] Waiting for verification request screen")
    VerificationRequestPage(driver, **page_args).share()
    logger.info("[verification_flow] Tapped Share")

    check_for_error(driver, "after share")

    if wait_present(driver, _home_id, timeout=default_timeout):
        logger.info(f"[verification_flow] Verification '{credential_name}' complete — home screen reached")
    else:
        logger.info(f"[verification_flow] Verification '{credential_name}' complete")
