import logging

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.hovi.flows import clear_stale_error
from wallets.hovi.pages.credential_offer_page import CredentialOfferPage
from wallets.hovi.pages.credential_offer_page import on_screen as _offer_on_screen
from wallets.hovi.pages.home_page import HomePage
from wallets.hovi.screens import SCREENS

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3

# Serviced on every poll pass rather than once after the deeplink. hovi's camera/notification
# permission prompt can appear at any point, and while it is up the offer screen underneath cannot
# be read — which used to surface as `absent`, a wrong statement about a wallet that was waiting
# for us.
_INTERSTITIALS = (interstitials.permission_dialog(),)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Hovi wallet.

    Flow:
      1. Clear any stale error banner (see flows/__init__.py)
      2. Background app (HOME keycode)
      3. Fire deeplink via mobile: deepLink, exactly as the provider gave it
      4. Wait for one of the known outcomes; accept the offer if it is the one we wanted
      5. Return to home screen

    The URL is fired unchanged whatever its scheme. Nothing is rewritten and nothing is refused up
    front. If the wallet could not receive it, `base/outcome.py` says so afterwards, with the
    device's own list of registered schemes as evidence.
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    t = ctx.timeout("credential_offer")
    ctx.url = provider.get(credential_name)

    error_before = clear_stale_error(driver, app_package, ctx.flow)

    logger.info("[credential_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": ctx.url, "package": app_package})
    except WebDriverException as exc:
        # Android refused to route the intent at all. Same conclusion as nothing appearing, so it
        # takes the same diagnosis rather than a second failure shape.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="credential offer",
                          timeout=t, error_before=error_before)

    logger.info("[credential_flow] Waiting for credential offer screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_offer_on_screen, timeout=t,
                             error_before=error_before, interstitials=_INTERSTITIALS)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="credential offer", timeout=t,
                          error_before=error_before, interstitials=_INTERSTITIALS)

    CredentialOfferPage(driver, **page_args).accept()
    logger.info("[credential_flow] Waiting for home screen after acceptance")
    HomePage(driver, **page_args).wait_until_loaded()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="accepted the offer for",
                              error_before=error_before)
