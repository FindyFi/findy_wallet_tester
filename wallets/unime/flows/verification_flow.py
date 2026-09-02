import logging
import time

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.unime.pages import pin_page as pin_screen
from wallets.unime.pages.pin_page import PinPage
from wallets.unime.pages.verification_request_page import VerificationRequestPage
from wallets.unime.pages.verification_request_page import on_screen as _request_on_screen
from wallets.unime.screens import SCREENS

logger = logging.getLogger(__name__)

_INTERSTITIALS = (
    interstitials.permission_dialog(),
    interstitials.screen_action(
        "app-password", pin_screen.on_screen,
        lambda driver, ctx: PinPage(driver, **ctx.page_args).enter_pin(ctx.pin),
    ),
)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a verification request deeplink and share credentials in the unime wallet.

    As in credential_flow, the app is not backgrounded first and the URL is never rewritten.
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    t = ctx.timeout("credential_offer")
    ctx.url = provider.get(credential_name)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": ctx.url, "package": app_package})
    except WebDriverException as exc:
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="information request",
                          timeout=t)

    time.sleep(3)

    logger.info("[verification_flow] Waiting for verification request screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_request_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="information request", timeout=t,
                          interstitials=_INTERSTITIALS)

    logger.info("[verification_flow] Verification request screen — sharing credentials")
    VerificationRequestPage(driver, **page_args).share()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="shared for")
    logger.info(f"[verification_flow] Credentials shared for '{credential_name}'")
