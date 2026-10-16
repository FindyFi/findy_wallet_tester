import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.procivis.pages import pin_page as pin_screen
from wallets.procivis.pages.pin_page import PinPage, biometric_on_screen
from wallets.procivis.pages.proof_request_sharing_page import (
    ProofRequestSharingPage,
    on_screen as sharing_on_screen,
    SCREEN_ID as _sharing_id,
)
from wallets.procivis.pages.proof_request_accept_process_page import (
    ProofRequestAcceptProcessPage,
    SCREEN_ID as _process_id,
)

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Procivis wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package (e.g. "ch.procivis.one.wallet.trial")
        pin:             PIN to unlock the app — required if the app is locked on deeplink
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    url = provider.get(credential_name)
    # From the background, as a tapped link arrives: see flows/_background.py for why, and for how
    # much of that is known. Imported with its one call so the two stay together.
    from wallets.procivis.flows._background import background
    background(driver, app_package, "verification_flow")
    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, url=url, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    t = ctx.timeout("credential_offer")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="proof request")

    failures.unlock(driver, ctx)

    try:
        shown = sharing_on_screen(driver, timeout=t)
    except WebDriverException as exc:
        raise failures.lost(ctx, "mid-wait for the proof request", exc) from exc
    if not shown:
        _no_request(driver, ctx, t)

    # Not `match`: procivis has no mapped no-match surface, so the sharing screen alone does not
    # prove it holds anything that satisfies the request.
    progress.reached(progress.PRESENT, "procivis showed the proof request")
    logger.info("[verification_flow] Sharing screen — sharing credentials")
    ProofRequestSharingPage(driver, **page_args).share()
    progress.step("tapped Share")

    _after_share(driver, ctx)

    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
