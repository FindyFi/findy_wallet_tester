import logging

from selenium.common.exceptions import WebDriverException

from base.android import handle_permission_if_present
from providers.base import DeeplinkProvider
from wallets.hovi.flows import outcome
from wallets.hovi.pages.verification_request_page import VerificationRequestPage
from wallets.hovi.pages.verification_request_page import on_screen as _request_on_screen
from wallets.hovi.pages.home_page import HomePage
from wallets.hovi.pages import error_page, no_match_page

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a verification request deeplink and share credentials in the Hovi wallet.

    Flow:
      1. Clear any stale error banner
      2. Background app (HOME keycode)
      3. Fire deeplink via mobile: deepLink, exactly as the provider gave it
      4. Handle camera permission if it appears (first run only)
      5. Wait for one of the known outcomes; share if it is the one we wanted
      6. Return to home screen

    As in credential_flow, the URL is never rewritten or refused up front.
    """
    url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    # The banner survives until the app restarts, so only one that appears after the deeplink
    # says anything about this request.
    error_before = error_page.present(driver, timeout=0.5)
    if error_before:
        if error_page.dismiss(driver, app_package):
            logger.info("[verification_flow] Cleared a stale hovi error banner before the deeplink")
            error_before = False
        else:
            logger.warning(
                "[verification_flow] A hovi error banner is stuck on screen before this deeplink, "
                "so it cannot be used to diagnose this case"
            )

    logger.info("[verification_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})
    except WebDriverException as exc:
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, "verification_flow", "information request",
                          credential_name, t, error_before, app_package, url)

    handle_permission_if_present(driver)

    logger.info("[verification_flow] Waiting for verification request screen")
    state = outcome.wait_for(driver, _request_on_screen, timeout=t, error_before=error_before)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, "verification_flow", "information request",
                          credential_name, t, error_before, app_package, url)

    # The request screen appearing is not the same as it offering anything to share. hovi renders
    # "No Credential Found" inside it when nothing matches.
    if no_match_page.present(driver):
        no_match_page.dismiss(driver)
        raise outcome.FlowFailure(outcome.NO_MATCH, (
            f"[verification_flow] hovi received the request for '{credential_name}' and answered "
            "\"No Credential Found: the credential is not present in your wallet\". It holds "
            "nothing that satisfies what this verifier asked for"
        ))

    VerificationRequestPage(driver, **page_args).share()
    logger.info("[verification_flow] Waiting for home screen after sharing")
    HomePage(driver, **page_args).wait_until_loaded()
    outcome.raise_if_rejected(driver, "verification_flow", credential_name, "shared for",
                              error_before)
