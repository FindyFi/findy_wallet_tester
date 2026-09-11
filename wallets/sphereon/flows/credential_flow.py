"""Fire a credential offer deeplink at sphereon and take whatever the wallet offers.

Walked live on 2026-09-10 against 0.9.0 (build 901). The wallet is not backgrounded first: it
processes the intent in the foreground, like unime and unlike heidi and hovi.

    deeplink -> "Getting information..." -> "Do you recognize <issuer>" [Yes, continue]
             -> (low-trust confirmation modal, for a party outside the wallet's federations)
             -> ... the credential offer, which nothing here has yet reached

**No configured issuer gets to the offer screen**, measured the same day, and the wallet names the
reason on screen every time — which is the whole point of mapping its error surface:

- `sphereon_issuer` — "Retrieving an access token ... failed with status: 400.
  Response: {"error":"invalid_request"}". Reproduced with curl against Sphereon's own agent
  outside the wallet entirely, so this one is the provider's: the offer at
  issuer.sphereon.pensiondemo.findy.fi carries the literal pre-authorized code
  "using the same code for all requests...", which the agent rejects. No wallet can take it.
- `hovi_issuer`, `paradym_issuer` — "Sending authorization challenge request", with no body. The
  wallet tries a first-party authorization challenge for these offers and the request fails.
- `procivis_issuer` — "Retrieving a credential ... failed with status: 400:
  invalid_or_missing_proof". The token step succeeds and the key-binding proof is refused.
- `waltid_issuer` — "Retrieving a credential ... failed with status: 400".

All five surface as `rejected` with the wallet's own message quoted, rather than as a timeout on a
missing Accept button. The offer page's locators are the one unproven part of this flow — see
`pages/credential_offer_page.py`, which also lists the two steps (credential selection, issuer
transaction code) that no capture has shown.
"""
import logging

from selenium.common.exceptions import WebDriverException

from base import interstitials, outcome
from base.flow_context import FlowContext
from providers.base import DeeplinkProvider
from wallets.sphereon.pages import contact_consent_page, pin_page as pin_screen
from wallets.sphereon.pages.contact_consent_page import ContactConsentPage
from wallets.sphereon.pages.credential_offer_page import CredentialOfferPage
from wallets.sphereon.pages.credential_offer_page import on_screen as _offer_on_screen
from wallets.sphereon.pages.home_page import on_screen as _home_on_screen
from wallets.sphereon.pages.pin_page import PinPage
from wallets.sphereon.screens import SCREENS

logger = logging.getLogger(__name__)

# Order is load-bearing twice over.
#
# The low-trust modal comes first because it *overlays* the consent screen rather than replacing
# it: both are in the tree together, both spell their confirm button "Yes, continue", and a tap
# aimed at the covered one lands on nothing.
#
# The lock screen is serviced too, because a deeplink can arrive at a wallet that has locked itself
# since the last test.
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
    """Open a credential offer deeplink and accept it in the sphereon wallet."""
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

    logger.info("[credential_flow] Waiting for the credential offer screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_offer_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)
    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="credential offer", timeout=t,
                          interstitials=_INTERSTITIALS)

    logger.info("[credential_flow] Credential offer screen — accepting")
    CredentialOfferPage(driver, **page_args).accept()
    outcome.raise_if_rejected(driver, SCREENS, ctx, action="accepted the offer for")

    # The wallet's help text promises a confirmation after acceptance, but no run has seen it, so
    # there is no success probe to wait on — home is the terminal state this waits for instead.
    # Anything else still on screen (that unmapped confirmation included) comes back named rather
    # than as a bare timeout in the test's credential count.
    logger.info("[credential_flow] Waiting for the wallet to return to its home screen")
    state = outcome.wait_for(driver, SCREENS, ctx, target=_home_on_screen, timeout=t,
                             interstitials=_INTERSTITIALS)
    if state != outcome.SUCCESS:
        outcome.raise_for(state, driver, SCREENS, ctx, expected="home screen", timeout=t,
                          interstitials=_INTERSTITIALS)
    logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
