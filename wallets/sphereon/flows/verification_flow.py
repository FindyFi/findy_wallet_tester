"""Fire a presentation request deeplink at sphereon and share what it asks for.

Walked live on 2026-09-10 against 0.9.0 (build 901) and `hovi_verifier`:

    deeplink -> "Getting information..." -> "Do you recognize <verifier>" [Yes, continue]
             -> (low-trust confirmation modal)
             -> "Information request" -> [Share] / [Decline]

The request screen was reached and read. What could not be exercised is a successful share: no
configured issuer can put a credential into this wallet (see `credential_flow.py`), so every
request so far has come back "No Available Credentials / 0 available" — reported as `no_match`,
which is a statement about the verifier's request meeting an under-stocked wallet, not about the
wallet failing.

`Share` is in the tree even then, and not clickable, so checking `no_match_page` before touching it
is what keeps that case from reading as a broken locator.
"""
import logging

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.sphereon.pages import contact_consent_page, no_match_page, pin_page as pin_screen
from wallets.sphereon.pages.contact_consent_page import ContactConsentPage
from wallets.sphereon.pages.home_page import on_screen as _home_on_screen
from wallets.sphereon.pages.pin_page import PinPage
from wallets.sphereon.pages.verification_request_page import VerificationRequestPage
from wallets.sphereon.pages.verification_request_page import on_screen as _request_on_screen
from wallets.sphereon.screens import SCREENS

logger = logging.getLogger(__name__)

# Same set, same order, and for the same reasons as in credential_flow: the low-trust modal
# overlays the consent screen and shares its button wording, so it has to be answered first.
_INTERSTITIALS = (
    interstitials.permission_dialog(),
    interstitials.screen_action(
        "low-trust-confirm", contact_consent_page.modal_on_screen,
        lambda driver, ctx: ContactConsentPage(driver, **ctx.page_args).confirm_low_trust(),
        extends=5.0,
    ),
    interstitials.screen_action(
        "contact-consent", contact_consent_page.on_screen,
        lambda driver, ctx: ContactConsentPage(driver, **ctx.page_args).confirm(),
        extends=5.0,
    ),
    interstitials.screen_action(
        "app-pin", pin_screen.on_screen,
        lambda driver, ctx: PinPage(driver, **ctx.page_args).enter_pin(ctx.pin),
    ),
)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the sphereon wallet."""
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

    logger.info("[verification_flow] Waiting for the information request screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_request_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)
    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="information request", timeout=t,
                          interstitials=_INTERSTITIALS)

    # Reaching the request screen is not the same as it having anything to offer: the refusal
    # renders inside the request, with Share still present and inert.
    if no_match_page.present(driver, timeout=2):
        outcome.raise_no_match(SCREENS, ctx, said=no_match_page.message(driver))

    logger.info("[verification_flow] Information request screen — sharing")
    VerificationRequestPage(driver, **page_args).share()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="shared for")

    logger.info("[verification_flow] Waiting for the wallet to return to its home screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_home_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)
    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="home screen", timeout=t,
                          interstitials=_INTERSTITIALS)
    logger.info(f"[verification_flow] Credentials shared for '{credential_name}'")
