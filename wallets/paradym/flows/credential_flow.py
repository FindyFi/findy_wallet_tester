import logging

from base.android import handle_permission_if_present
from base.utils import wait_present
from wallets.paradym.flows import GO_TO_WALLET as _go_to_wallet, check_for_error
from wallets.paradym.pages.authenticate_page import AuthenticatePage
from wallets.paradym.pages import authenticate_page as authenticate_screen
from wallets.paradym.pages.card_offered_page import CardOfferedPage
from wallets.paradym.pages import card_offered_page as card_offered_screen
from wallets.paradym.pages.tx_code_page import TxCodePage
from wallets.paradym.pages import tx_code_page as tx_code_screen
from wallets.paradym.pages.credential_offer_page import CredentialOfferPage
from wallets.paradym.pages.issuer_consent_page import IssuerConsentPage
from wallets.paradym.pages import issuer_consent_page as consent_screen
from wallets.paradym.pages import pin_page as pin_screen
from wallets.paradym.pages.pin_page import PinPage
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pid", "mdl")
        app_package:     App package to handle the deeplink (e.g. "id.paradym.wallet")
        pin:             PIN to unlock the app if it locks after the deeplink is opened
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    url = provider.get(credential_name)
    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    handle_permission_if_present(driver)

    if pin_screen.on_screen(driver):
        if not pin:
            raise RuntimeError(
                "App showed PIN screen after deeplink — pass pin= to credential_flow.run()"
            )
        logger.info("[credential_flow] PIN screen appeared — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)

    # Stage 1: deeplink should have triggered the credential flow.
    # If an error screen appeared instead, raise immediately rather than waiting for timeouts.
    check_for_error(driver, "after deeplink")

    if consent_screen.on_screen(driver):
        logger.info("[credential_flow] Issuer consent screen — confirming")
        IssuerConsentPage(driver, **page_args).confirm()
        # Stage 2: issuer may reject the request after consent.
        check_for_error(driver, "after consent")

    if authenticate_screen.on_screen(driver):
        logger.info("[credential_flow] Authenticate screen — tapping Authenticate")
        AuthenticatePage(driver, **page_args).authenticate()
        check_for_error(driver, "after authenticate")

    if card_offered_screen.on_screen(driver):
        logger.info("[credential_flow] Card offered screen — continuing")
        CardOfferedPage(driver, **page_args).continue_()
        check_for_error(driver, "after card offered")

    tx_code = getattr(provider, "last_tx_code", None)
    if tx_code and tx_code_screen.on_screen(driver):
        logger.info("[credential_flow] TX code screen — entering code")
        TxCodePage(driver, **page_args).enter_code(tx_code)
        check_for_error(driver, "after tx_code")

    CredentialOfferPage(driver, **page_args).accept()

    # Stage 3: wallet may fail to store the credential after acceptance.
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    if wait_present(driver, _go_to_wallet, timeout=default_timeout):
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted — navigating to wallet")
        driver.find_element(*_go_to_wallet).click()
    else:
        check_for_error(driver, "after accept")
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
