import logging

from base.android import handle_permission_if_present
from providers.base import DeeplinkProvider
from wallets.hovi.pages.credential_offer_page import CredentialOfferPage
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Hovi wallet.

    Flow:
      1. Background app (HOME keycode)
      2. Fire deeplink via mobile: deepLink
      3. Handle camera permission if it appears (first run only)
      4. Wait for credential offer screen and accept
      5. Return to home screen
    """
    url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})

    logger.info("[credential_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    handle_permission_if_present(driver)

    logger.info("[credential_flow] Waiting for credential offer screen")
    CredentialOfferPage(driver, **page_args).accept()

    logger.info("[credential_flow] Waiting for home screen after acceptance")
    HomePage(driver, **page_args).wait_until_loaded()
