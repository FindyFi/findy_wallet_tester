import logging

from appium.webdriver.common.appiumby import AppiumBy

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

    # Toppan shows a result screen before returning home. Both outcomes show
    # "Back to Home Page", so we must identify the state before navigating away:
    #   Rejection: "No document shared"               — verifier refused the presentation
    #   Success:   "Information Shared" and/or
    #              "The following was securely sent"   — verifier accepted, credentials sent
    #   Unknown:   neither indicator present           — unexpected screen, do not retry
    _back_btn    = (AppiumBy.XPATH, '//*[@text="Back to Home Page"]')
    _no_doc      = (AppiumBy.XPATH, '//*[@text="No document shared"]')
    _info_shared = (AppiumBy.XPATH, '//*[@text="Information Shared"]')
    _info_sent   = (AppiumBy.XPATH, '//*[@text="The following was securely sent"]')
    if wait_present(driver, _back_btn, timeout=default_timeout):
        is_rejected = wait_present(driver, _no_doc, timeout=1)
        is_success  = (
            wait_present(driver, _info_shared, timeout=1)
            or wait_present(driver, _info_sent, timeout=1)
        )
        try:
            driver.find_element(*_back_btn).click()
            wait_present(driver, _home_id, timeout=default_timeout)
        except Exception:
            pass
        if is_rejected:
            logger.warning(f"[verification_flow] Verifier rejected '{credential_name}' — No document shared")
            raise RuntimeError(
                f"Verification '{credential_name}' failed — verifier reported: No document shared [no_retry]"
            )
        if is_success:
            logger.info(f"[verification_flow] Verification '{credential_name}' complete — Information Shared")
            return
        raise RuntimeError(
            f"Verification '{credential_name}' — unknown result screen: "
            "neither 'No document shared' nor 'Information Shared' / 'The following was securely sent' found [no_retry]"
        )

    if wait_present(driver, _home_id, timeout=default_timeout):
        logger.info(f"[verification_flow] Verification '{credential_name}' complete — home screen reached")
    else:
        logger.info(f"[verification_flow] Verification '{credential_name}' complete")
