import logging

from base.android import handle_permission_if_present
from base.utils import wait_present
from wallets.paradym.flows import GO_TO_WALLET as _go_to_wallet, check_for_error
from wallets.paradym.pages.issuer_consent_page import IssuerConsentPage
from wallets.paradym.pages import issuer_consent_page as consent_screen
from wallets.paradym.pages import pin_page as pin_screen
from wallets.paradym.pages.pin_page import PinPage
from wallets.paradym.pages.verification_request_page import VerificationRequestPage
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package to handle the deeplink (e.g. "id.paradym.wallet")
        pin:             PIN to unlock the app if it locks after the deeplink is opened
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    url = provider.get(credential_name)
    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    check_for_error(driver, "after deeplink")

    handle_permission_if_present(driver)

    if pin_screen.on_screen(driver, timeout=default_timeout):
        if not pin:
            raise RuntimeError(
                "App showed PIN screen after deeplink — pass pin= to verification_flow.run()"
            )
        logger.info("[verification_flow] PIN screen appeared — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)
        check_for_error(driver, "after PIN")

    if consent_screen.on_screen(driver, timeout=default_timeout):
        logger.info("[verification_flow] Verifier consent screen — confirming")
        IssuerConsentPage(driver, **page_args).confirm()

    if wait_present(driver, _go_to_wallet, timeout=default_timeout):
        raise RuntimeError(
            f"Verifier returned an error or rejected the '{credential_name}' request "
            "— 'Go to wallet' appeared instead of the share screen. "
            "This verifier may be incompatible with this wallet."
        )

    logger.info("[verification_flow] Review request screen — sharing")
    VerificationRequestPage(driver, **page_args).share()

    if pin_screen.on_screen(driver, timeout=default_timeout):
        if not pin:
            raise RuntimeError(
                "App showed PIN screen after Share — pass pin= to verification_flow.run()"
            )
        logger.info("[verification_flow] PIN confirmation screen — entering PIN")
        PinPage(driver, **page_args).enter_pin(pin)
    if wait_present(driver, _go_to_wallet, timeout=default_timeout):
        logger.info(f"[verification_flow] Credential '{credential_name}' shared — navigating to wallet")
        driver.find_element(*_go_to_wallet).click()
    else:
        logger.info(f"[verification_flow] Credential '{credential_name}' shared")
