import logging

from selenium.common.exceptions import WebDriverException

from base.android import handle_permission_if_present
from providers.base import DeeplinkProvider
from wallets.hovi.flows import outcome
from wallets.hovi.pages.credential_offer_page import CredentialOfferPage
from wallets.hovi.pages.credential_offer_page import on_screen as _offer_on_screen
from wallets.hovi.pages.home_page import HomePage
from wallets.hovi.pages import error_page

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Hovi wallet.

    Flow:
      1. Clear any stale error banner (see below)
      2. Background app (HOME keycode)
      3. Fire deeplink via mobile: deepLink, exactly as the provider gave it
      4. Handle camera permission if it appears (first run only)
      5. Wait for one of the known outcomes; accept the offer if it is the one we wanted
      6. Return to home screen

    The URL is fired unchanged whatever its scheme. Nothing is rewritten and nothing is refused up
    front. If the wallet could not receive it, `flows/outcome.py` says so afterwards, with the
    device's own list of registered schemes as evidence.
    """
    url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    # The banner survives until the app restarts, so a leftover one would make every later case
    # look rejected. Clear it first; only if it refuses to go is the error check disabled here.
    error_before = error_page.present(driver, timeout=0.5)
    if error_before:
        if error_page.dismiss(driver, app_package):
            logger.info("[credential_flow] Cleared a stale hovi error banner before the deeplink")
            error_before = False
        else:
            logger.warning(
                "[credential_flow] A hovi error banner is stuck on screen before this deeplink, "
                "so it cannot be used to diagnose this case"
            )

    logger.info("[credential_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    except WebDriverException as exc:
        # Android refused to route the intent at all. Same conclusion as nothing appearing, so it
        # takes the same diagnosis rather than a second failure shape.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, "credential_flow", "credential offer",
                          credential_name, t, error_before, app_package, url)

    handle_permission_if_present(driver)

    logger.info("[credential_flow] Waiting for credential offer screen")
    state = outcome.wait_for(driver, _offer_on_screen, timeout=t, error_before=error_before)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, "credential_flow", "credential offer",
                          credential_name, t, error_before, app_package, url)

    CredentialOfferPage(driver, **page_args).accept()
    logger.info("[credential_flow] Waiting for home screen after acceptance")
    HomePage(driver, **page_args).wait_until_loaded()
    outcome.raise_if_rejected(driver, "credential_flow", credential_name, "accepted the offer for",
                              error_before)
