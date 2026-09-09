import logging
import time

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.unime.pages import pin_page as pin_screen
from wallets.unime.pages.credential_offer_page import CredentialOfferPage
from wallets.unime.pages.credential_offer_page import on_screen as _offer_on_screen
from wallets.unime.pages.pin_page import PinPage
from wallets.unime.screens import SCREENS

logger = logging.getLogger(__name__)

# unime asks for its password after the deeplink, but not always and not at a fixed moment, so it
# is serviced on every poll pass rather than probed once. The permission dialog is the same story.
_INTERSTITIALS = (
    interstitials.permission_dialog(),
    interstitials.screen_action(
        "app-password", pin_screen.on_screen,
        lambda driver, ctx: PinPage(driver, **ctx.page_args).enter_pin(ctx.pin),
    ),
)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the unime wallet.

    unime is deliberately *not* backgrounded first: unlike heidi and hovi it processes the intent
    with the app in the foreground. The HOME press was commented out here for months with the
    keycode left behind as dead code; it is now simply absent.
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    t = ctx.timeout("credential_offer")
    ctx.url = provider.get(credential_name)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        driver.execute_script("mobile: deepLink", {"url": ctx.url, "package": app_package})
    except WebDriverException as exc:
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="credential offer",
                          timeout=t)

    # unime needs a moment before anything it will show is on screen; polling immediately just
    # burns ticks against the home screen it is still displaying.
    time.sleep(3)

    logger.info("[credential_flow] Waiting for credential offer screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_offer_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)

    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="credential offer", timeout=t,
                          interstitials=_INTERSTITIALS)

    logger.info("[credential_flow] Credential offer screen — accepting")
    CredentialOfferPage(driver, **page_args).accept()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="accepted the offer for")
    logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
