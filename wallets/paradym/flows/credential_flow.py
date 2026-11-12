import logging

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.paradym.flows import _failures as failures
from wallets.paradym.pages import error_page, result_page
from wallets.paradym.pages.authenticate_page import AuthenticatePage
from wallets.paradym.pages.authenticate_page import on_screen as authenticate_on_screen
from wallets.paradym.pages.card_offered_page import CardOfferedPage
from wallets.paradym.pages.card_offered_page import on_screen as card_offered_on_screen
from wallets.paradym.pages.credential_offer_page import (
    CredentialOfferPage,
    fetching_on_screen,
    on_screen as offer_on_screen,
)
from wallets.paradym.pages.issuer_consent_page import IssuerConsentPage
from wallets.paradym.pages.issuer_consent_page import on_screen as consent_on_screen
from wallets.paradym.pages.pin_page import on_screen as pin_on_screen
from wallets.paradym.pages.tx_code_page import TxCodePage
from wallets.paradym.pages.tx_code_page import on_screen as tx_code_on_screen
from wallets.paradym.screens import SCREENS

logger = logging.getLogger(__name__)

_EXPECTED = "credential offer"


def _until_offer(driver, ctx: FlowContext, t: float, tx_code: str) -> None:
    """Answer paradym's screens between the deeplink and the offer, in whatever order they come.
    Returns once the offer (its Accept) is up; raises a FlowFailure otherwise.

    Seen on passing runs (nightly 2026-10-27, test.log): PIN, the issuer consent ("Yes,
    continue"), "Card offered" (Continue), then the offer. The authenticate and transaction-code
    screens are the old flow's, kept as it had them; no log in reports/ or the nightly clone shows
    either. Each is answered at most once — one still up when a wait runs out is paradym asking
    again, not an unknown screen — and every wait for the next one gets the full `t`.
    """
    head = f"[credential_flow] paradym could not process the credential offer for '{ctx.what}'"
    error = [("error", error_page.present, 0.5)]
    steps = [("its PIN screen", pin_on_screen, 0.5),
             ("its issuer consent", consent_on_screen, 0.5),
             ("its authenticate screen", authenticate_on_screen, 0.5),
             ("its card-offered screen", card_offered_on_screen, 0.5)]
    if tx_code:
        steps.append(("its transaction-code screen", tx_code_on_screen, 0.5))
    offer = [("the credential offer", offer_on_screen, 1.0)]
    answered = []
    after, in_front = "the deeplink", False

    while True:
        seen = failures.first_of(driver, ctx, error + steps + offer, t,
                                 during="while waiting for the credential offer")
        if not seen:
            seen = failures.nothing_appeared(
                driver, ctx, late=steps + offer, timeout=t, expected=_EXPECTED,
                waited_for="the credential offer", after=after, head=head,
                was_in_front=in_front, fetching=fetching_on_screen, answered=answered)
        if seen == "error":
            progress.reached(progress.OPEN, "paradym was in front, showing its error screen")
            raise failures.error_failure(driver, ctx, head)
        if seen == "the credential offer":
            return

        answered += [s for s in steps if s[0] == seen]
        steps = [s for s in steps if s[0] != seen]
        in_front = True
        if seen == "its PIN screen":
            failures.enter_pin(driver, ctx, "after the deeplink")
            after = "the PIN"
            continue

        progress.reached(progress.OPEN, f"paradym showed {seen}")
        try:
            if seen == "its issuer consent":
                IssuerConsentPage(driver, **ctx.page_args).confirm()
                done, after = "confirmed the issuer consent", "the consent"
            elif seen == "its authenticate screen":
                AuthenticatePage(driver, **ctx.page_args).authenticate()
                done, after = "tapped Authenticate", "Authenticate"
            elif seen == "its card-offered screen":
                CardOfferedPage(driver, **ctx.page_args).continue_()
                done, after = "tapped Continue on the card-offered screen", "Continue"
            else:
                TxCodePage(driver, **ctx.page_args).enter_code(tx_code)
                done, after = "entered the transaction code", "the transaction code"
        except WebDriverException as exc:
            raise failures.lost(ctx, f"while answering {seen}", exc) from exc
        logger.info(f"[credential_flow] {done}")
        progress.step(done)


def _after_accept(driver, ctx: FlowContext, t: float) -> None:
    """Wait for paradym to finish with an accepted offer. Returns once its Go to wallet is up with
    no error heading, after tapping it (`failures.finish`); raises a FlowFailure otherwise — never
    the shared "nothing appeared", because paradym had already shown the offer and taken the tap.
    """
    head = f"[credential_flow] paradym took the Accept for '{ctx.what}' and then failed"
    watch = [("error", error_page.present, 0.5),
             ("success", result_page.go_to_wallet_on_screen, 1.0)]
    seen = failures.first_of(driver, ctx, watch, t, during="after Accept")
    if not seen:
        seen = failures.late_look(driver, ctx, watch, during="after Accept")
    if failures.finish(driver, ctx, seen, after="Accept", head=head):
        return

    failures.raise_if_crashed(driver, ctx, when="after Accept")
    raise FlowFailure(outcome.PROCESSING, (
        f"[credential_flow] paradym showed neither its Go to wallet nor its error screen in the "
        f"{t:.0f}s after Accept for '{ctx.what}', so whether it stored the credential is unknown"
    ))


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the wallet.

    Every verdict this flow reaches about paradym is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far paradym got. Taps through a page object can
    still raise that page's own generic error when its button cannot be found.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pid", "mdl")
        app_package:     App package to handle the deeplink (e.g. "id.paradym.wallet")
        pin:             PIN to unlock the app if it locks after the deeplink is opened
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin, page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("credential_offer")

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected=_EXPECTED)

    failures.answer_permission(driver, ctx)
    _until_offer(driver, ctx, t, getattr(provider, "last_tx_code", None) or "")

    progress.reached(progress.PRESENT, "paradym showed the credential offer")
    logger.info("[credential_flow] Credential offer — accepting")
    try:
        CredentialOfferPage(driver, **page_args).accept()
    except WebDriverException as exc:
        raise failures.lost(ctx, "while tapping Accept", exc) from exc
    progress.step("tapped Accept")

    _after_accept(driver, ctx, t)
    logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
