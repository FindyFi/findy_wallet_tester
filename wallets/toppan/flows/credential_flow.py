import logging
from typing import NoReturn
import time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.toppan.flows import _failures as failures
from wallets.toppan.pages.credential_offer_page import (
    ConfirmationPage,
    CredentialOfferPage,
    confirmation_on_screen,
    on_screen as offer_on_screen,
    processing_on_screen,
)
from wallets.toppan.pages.home_page import on_screen as home_on_screen
from wallets.toppan.screens import SCREENS

logger = logging.getLogger(__name__)

# How long after Accept toppan's wallet screen is not yet taken as the end of the flow, unless its
# processing overlay was seen and has cleared. Kept from the old flow, which waited this long for
# the overlay before looking for home: the wallet screen sits behind the offer, and seeing it
# before toppan has started storing would let the count run too early.
_SETTLE = 5.0

# One more look for toppan's error dialog once its wallet screen is back. 70 of the old flow's
# failures were dialogs found "[after processing]" — authbound_issuer 36, sphereon_issuer 30,
# paradym_issuer 4, across reports/ and the nightly clone — so the dialog follows the overlay, and
# no dump shows whether the wallet screen can come back first. It costs 1.5 s on every pass.
_LAST_ERROR_LOOK = 1.5


def _after_accept(driver, ctx: FlowContext, t: float) -> None:
    """Wait for toppan to finish with an accepted offer. Returns once its wallet screen is back
    with no error dialog; raises a FlowFailure otherwise — never the shared "nothing appeared",
    because toppan had already shown the offer and taken the tap.
    """
    start = time.monotonic()
    saw_processing = False
    error_present = failures.error_probe(ctx)

    def look() -> str:
        nonlocal saw_processing
        if error_present(driver, 0.5):
            return "error"
        if processing_on_screen(driver, 0.5):
            if not saw_processing:
                progress.step("toppan showed its processing overlay")
            saw_processing = True
            return "processing"
        settled = saw_processing or time.monotonic() - start >= _SETTLE
        if settled and home_on_screen(driver, 1.0):
            return "home"
        return ""

    try:
        seen = look()
        while seen not in ("error", "home") and time.monotonic() - start < t:
            seen = look()
        if seen not in ("error", "home"):
            seen = look()  # once more after the deadline: a late answer still counts
        if seen == "home" and error_present(driver, _LAST_ERROR_LOOK):
            seen = "error"
    except WebDriverException as exc:
        raise failures.lost(ctx, "after Accept", exc) from exc

    if seen == "error":
        raise failures.dialog_failure(driver, ctx, (
            f"[credential_flow] toppan took the Accept for '{ctx.what}' and then failed"))
    if seen == "home":
        progress.reached(progress.ACCEPT,
                         "toppan returned to its wallet screen with no error dialog")
        return

    failures.raise_if_crashed(driver, ctx, when="after Accept")
    if seen == "processing":
        raise FlowFailure(outcome.PROCESSING, (
            f"[credential_flow] toppan's processing overlay was still up when checked "
            f"{t:.0f}s after Accept for '{ctx.what}'"
        ))
    raise FlowFailure(outcome.PROCESSING, (
        f"[credential_flow] toppan showed neither its wallet screen nor an error dialog in the "
        f"{t:.0f}s after Accept for '{ctx.what}', so whether it stored the credential is unknown"
    ))


def _tap_failed(driver, ctx: FlowContext, exc: Exception, *, late, waited_for: str, after: str,
                head: str) -> NoReturn:
    """A tap's button could not be found, though its screen had shown a moment before. Raises.

    `BasePage.click` raises exactly `Exception` when the button is not clickable in time; anything
    else is not that and propagates untouched. The screen is then looked at once more, as after
    any wait that ran out: what is there now is the diagnosis. If it is the same screen, the
    button alone is missing and the page's own error stands.
    """
    if type(exc) is not Exception:
        raise exc
    failures.nothing_appeared(driver, ctx, late=late, timeout=ctx.timeout("default"),
                              expected="credential offer", waited_for=waited_for, after=after,
                              head=head, was_in_front=True)
    raise exc


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Toppan wallet.

    Flow:
      1. Fire deeplink
      2. "Do you want to proceed with OpenID4VCI issuance?" dialog → CONTINUE
      3. "OID4VCI Issuance" credential offer modal → Accept
      4. Wait for "Adding your credential" to clear and the wallet screen to return

    Every verdict this flow reaches about toppan is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far toppan got. A tap on CONTINUE or Accept whose
    button is gone is diagnosed like any other wait that ran out (`_tap_failed`); only a button
    that is missing while its screen is still up stays the page's own generic error. toppan has no
    PIN, so `pin` is unused.
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
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="credential offer")

    failures.answer_permission(driver, ctx)

    head = (f"[credential_flow] toppan could not process the credential offer for "
            f"'{credential_name}'")
    error = [("error", failures.error_probe(ctx), 0.5)]
    confirm = [("its issuance confirmation", confirmation_on_screen, 1.0)]
    seen = failures.first_of(driver, ctx, confirm + error, t,
                             during="while waiting for the issuance confirmation")
    if seen == "error":
        progress.reached(progress.OPEN, "toppan was in front, showing its error dialog")
        raise failures.dialog_failure(driver, ctx, head)
    if not seen:
        failures.nothing_appeared(driver, ctx, late=confirm, timeout=t, expected="credential offer",
                                  waited_for="its issuance confirmation", after="the deeplink",
                                  head=head)

    progress.reached(progress.OPEN, "toppan asked whether to proceed with OpenID4VCI issuance")
    logger.info("[credential_flow] Confirmation dialog — tapping CONTINUE")
    try:
        ConfirmationPage(driver, **page_args).confirm()
    except WebDriverException as exc:
        raise failures.lost(ctx, "while tapping CONTINUE", exc) from exc
    except Exception as exc:
        _tap_failed(driver, ctx, exc, late=confirm, waited_for="its CONTINUE button",
                    after="the issuance confirmation showed", head=head)
    progress.step("tapped CONTINUE")

    offer = [("the credential offer", offer_on_screen, 1.0)]
    seen = failures.first_of(driver, ctx, offer + error, t,
                             during="while waiting for the credential offer")
    if seen == "error":
        raise failures.dialog_failure(driver, ctx, head)
    if not seen:
        failures.nothing_appeared(driver, ctx, late=offer, timeout=t, expected="credential offer",
                                  waited_for="the credential offer", after="CONTINUE", head=head,
                                  was_in_front=True)

    progress.reached(progress.PRESENT, "toppan showed the credential offer")
    logger.info("[credential_flow] Credential offer screen — accepting")
    try:
        CredentialOfferPage(driver, **page_args).accept()
    except WebDriverException as exc:
        raise failures.lost(ctx, "while tapping Accept", exc) from exc
    except Exception as exc:
        _tap_failed(driver, ctx, exc, late=offer, waited_for="its Accept button",
                    after="the credential offer showed", head=head)
    progress.step("tapped Accept")

    _after_accept(driver, ctx, t)
    logger.info(f"[credential_flow] Credential '{credential_name}' accepted — "
                "wallet screen reached")
