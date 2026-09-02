import logging

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.hovi.flows import clear_stale_error
from wallets.hovi.pages import no_match_page
from wallets.hovi.pages.home_page import HomePage
from wallets.hovi.pages.verification_request_page import VerificationRequestPage
from wallets.hovi.pages.verification_request_page import on_screen as _request_on_screen
from wallets.hovi.screens import SCREENS

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3

_INTERSTITIALS = (interstitials.permission_dialog(),)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a verification request deeplink and share credentials in the Hovi wallet.

    Flow:
      1. Clear any stale error banner
      2. Background app (HOME keycode)
      3. Fire deeplink via mobile: deepLink, exactly as the provider gave it
      4. Wait for one of the known outcomes; share if it is the one we wanted
      5. Return to home screen

    As in credential_flow, the URL is never rewritten or refused up front.
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    t = ctx.timeout("credential_offer")
    ctx.url = provider.get(credential_name)

    error_before = clear_stale_error(driver, app_package, ctx.flow)

    logger.info("[verification_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": ctx.url, "package": app_package})
    except WebDriverException as exc:
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="information request",
                          timeout=t, error_before=error_before)

    logger.info("[verification_flow] Waiting for verification request screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_request_on_screen, timeout=t,
                             error_before=error_before, interstitials=_INTERSTITIALS)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="information request", timeout=t,
                          error_before=error_before, interstitials=_INTERSTITIALS)

    # The request screen appearing is not the same as it offering anything to share. hovi renders
    # "No Credential Found" inside it when nothing matches.
    if no_match_page.present(driver):
        no_match_page.dismiss(driver)
        outcome.raise_no_match(SCREENS, ctx, said=(
            "No Credential Found: the credential is not present in your wallet"))

    VerificationRequestPage(driver, **page_args).share()
    logger.info("[verification_flow] Waiting for home screen after sharing")
    HomePage(driver, **page_args).wait_until_loaded()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="shared for",
                              error_before=error_before)
