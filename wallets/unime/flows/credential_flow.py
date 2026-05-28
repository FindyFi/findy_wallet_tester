import logging
import time

from base.android import handle_permission_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.unime.pages import pin_page as pin_screen
from wallets.unime.pages.pin_page import PinPage
from wallets.unime.pages.home_page import SCREEN_ID as _home_id
from wallets.unime.pages.credential_offer_page import (
    CredentialOfferPage,
    SCREEN_ID as _offer_id,
)

logger = logging.getLogger(__name__)
_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("credential_offer", timeouts.get("default", 30))

    # logger.info("[credential_flow] Backgrounding app before deeplink")
    # driver.press_keycode(_KEYCODE_HOME)
    # time.sleep(2)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    time.sleep(3)

    handle_permission_if_present(driver)

    if pin_screen.on_screen(driver, timeout=3):
        if not pin:
            raise RuntimeError(
                "App showed password screen after deeplink — pass pin= to credential_flow.run()"
            )
        logger.info("[credential_flow] Password screen — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)

    if wait_present(driver, _offer_id, timeout=default_timeout):
        logger.info("[credential_flow] Credential offer screen — accepting")
        CredentialOfferPage(driver, **page_args).accept()
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
        return

    if wait_present(driver, _home_id, timeout=3):
        raise RuntimeError(
            f"[credential_flow] App returned to home without showing an offer screen for "
            f"'{credential_name}' — deeplink may not have been processed"
        )

    raise RuntimeError(
        f"[credential_flow] No recognisable screen appeared after deeplink "
        f"for '{credential_name}' (timed out after {default_timeout}s)"
    )
