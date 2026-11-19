import logging
import time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.gataca.flows import _failures as failures
from wallets.gataca.pages import credential_offer_page as offer_page
from wallets.gataca.pages import error_page, success_page
from wallets.gataca.pages.credential_offer_page import CredentialOfferPage
from wallets.gataca.pages.home_page import on_screen as home_on_screen
from wallets.gataca.pages.success_page import SuccessPage
from wallets.gataca.screens import SCREENS

logger = logging.getLogger(__name__)

_EXPECTED = "verification request"

# "Login Successful" answered more than this many times is the dialog staying up after its OK.
_MAX_LOGIN_TAPS = 3


def _watch(ctx: FlowContext, state: dict) -> dict:
    """gataca's screens, as (name, probe, probe timeout). The system auth sheet is a probe that
    answers it: `first_of` then reports it and the caller carries on waiting."""
    return {
        "auth": ("the system auth sheet",
                 lambda d, w: failures.answer_auth(d, ctx, state["when"], detect_timeout=w), 0.4),
        "error": ("error", error_page.present, 0.5),
        "no match": ("no match", offer_page.no_match, 0.5),
        "rejected": ("rejected", success_page.on_rejected_screen, 0.5),
        # Before home: "My credentials" is still in the tree under these modals.
        "complete": ("Verification Complete", success_page.on_complete_screen, 0.5),
        "shared": ("Credentials Shared", success_page.on_terminal_screen, 0.5),
        "login": ("Login Successful", success_page.on_login_screen, 0.5),
        # "Sending your consent" lies over home, whose heading stays in the tree beneath it
        # (2.5.9 dumps): so before home, or home passes while gataca is still sending.
        "processing": ("processing", offer_page.processing, 0.5),
        "request": ("the verification request", offer_page.on_screen, 1.0),
        "home": ("home", home_on_screen, 1.0),
    }


def _until_request(driver, ctx: FlowContext, t: float, state: dict, w: dict) -> None:
    """Answer gataca's screens between the deeplink and the request — the system auth sheet some
    verifiers raise first. Returns once Sharing requirements is up with nothing in the way."""
    head = (f"[verification_flow] gataca could not process the verification request for "
            f"'{ctx.what}'")
    watch = [w["auth"], w["error"], w["no match"], w["request"]]
    after, in_front = "the deeplink", False
    while True:
        seen = failures.first_of(driver, ctx, watch, t,
                                 during="while waiting for the verification request")
        if not seen:
            seen = failures.nothing_appeared(
                driver, ctx, late=watch[2:], timeout=t, expected=_EXPECTED,
                waited_for="the verification request", after=after, head=head,
                was_in_front=in_front, fetching=offer_page.processing)
        if seen == "the system auth sheet":
            after = "the system auth sheet"
            continue
        if seen == "error":
            progress.reached(progress.OPEN, "gataca was in front, showing its error screen")
            raise failures.error_failure(driver, ctx, head)
        if seen == "no match":
            # The dialog lies over the request: shown, and holding nothing that answers it. Left
            # up for the failure screenshot and dump; init_flow recovers in teardown.
            progress.reached(progress.PRESENT, "gataca showed the request under its no-match dialog")
            raise failures.no_match_failure(driver, ctx)
        progress.reached(progress.OPEN, "gataca showed Sharing requirements")
        # The no-match dialog and Share are in the tree together, and the dialog can render a
        # moment after the screen: one more look before claiming a match.
        if offer_page.no_match(driver, 1.0):
            progress.reached(progress.PRESENT, "gataca showed the request under its no-match dialog")
            raise failures.no_match_failure(driver, ctx)
        return


def _after_share(driver, ctx: FlowContext, t: float, state: dict, w: dict) -> str:
    """Wait for gataca's answer after Share, answering the system auth sheet and "Login
    Successful" on the way. Returns how it ended; raises a FlowFailure otherwise — never the
    shared "nothing appeared", because gataca had already shown the request and taken the tap.

    "Verification Complete" and "Credentials Shared" are modals over home, so both are probed
    before it, and so is the "Sending your consent" overlay, which waits (up to `t` after the last
    thing gataca did) rather than ending the flow on the home beneath it. A bare home passes
    without claiming `accept`: nothing on it says the verifier got the presentation.
    """
    head = f"[verification_flow] gataca took the Share for '{ctx.what}' and then failed"
    watch = [w["auth"], w["error"], w["rejected"], w["no match"], w["complete"], w["shared"],
             w["login"], w["processing"], w["home"]]
    sending_until = time.monotonic() + t
    while True:
        seen = failures.first_of(driver, ctx, watch, t, during="after Share")
        if not seen:
            seen = failures.late_look(driver, ctx, watch[1:] + [w["request"]], during="after Share")
            if seen == "the verification request":
                raise FlowFailure(outcome.PROMPT_LOOP, (
                    f"[verification_flow] gataca was showing the verification request again "
                    f"{t:.0f}s after Share for '{ctx.what}', although it had already been "
                    "answered, instead of progressing"
                ))
            if not seen:
                failures.raise_if_crashed(driver, ctx, when="after Share")
                raise FlowFailure(outcome.PROCESSING, (
                    f"[verification_flow] gataca showed no Verification Complete, no Credentials "
                    f"Shared, no home screen and no error screen in the {t:.0f}s after Share for "
                    f"'{ctx.what}'. Whether the presentation reached the verifier is unknown"
                ))

        if seen == "processing":
            if time.monotonic() >= sending_until:
                progress.reached(progress.OPEN, "gataca was in front, sending its consent")
                raise FlowFailure(outcome.PROCESSING, (
                    f"[verification_flow] gataca was still on 'Sending your consent' {t:.0f}s "
                    f"after its last answer for '{ctx.what}'. Whether the presentation reached "
                    "the verifier is unknown"
                ))
            continue
        sending_until = time.monotonic() + t
        if seen == "the system auth sheet":
            continue
        if seen == "error":
            raise failures.error_failure(driver, ctx, head)
        if seen == "rejected":
            progress.reached(progress.OPEN, "gataca was in front, showing its Rejected dialog")
            raise failures.rejected_failure(driver, ctx)
        if seen == "no match":
            progress.reached(progress.PRESENT, "gataca showed the request under its no-match dialog")
            raise failures.no_match_failure(driver, ctx)

        if seen == "Login Successful":
            taps = ctx.prompt_budget.get("login", 0) + 1
            ctx.prompt_budget["login"] = taps
            if taps > _MAX_LOGIN_TAPS:
                raise FlowFailure(outcome.PROMPT_LOOP, (
                    f"[verification_flow] gataca kept showing Login Successful for '{ctx.what}' "
                    f"after {taps - 1} OK taps instead of progressing"
                ))
            logger.info("[verification_flow] Login Successful — tapping OK")
            failures.tap(ctx, "while confirming Login Successful",
                         lambda: SuccessPage(driver, **ctx.page_args).confirm())
            progress.step("confirmed Login Successful")
            state["when"] = "after Login Successful"
            continue

        if seen in ("Verification Complete", "Credentials Shared"):
            logger.info(f"[verification_flow] {seen} matched — tapping OK")
            progress.reached(progress.ACCEPT, f"gataca showed {seen}")
            try:
                failures.tap(ctx, f"while tapping OK on {seen}",
                             lambda: SuccessPage(driver, **ctx.page_args).confirm())
            except FlowFailure:
                raise
            except Exception as exc:
                # The answer is already in; a dialog with no tappable OK is not a failure of it.
                logger.warning(f"[verification_flow] No OK to tap on {seen}: {exc}")
            return seen

        # home
        progress.step("gataca returned to its home screen after Share, with no dialog "
                      "(accept not claimed)")
        return "its home screen"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Gataca wallet.

    Flow: deeplink → [system auth sheet] → Sharing requirements → Share → system auth sheet →
    Verification Complete (or Login Successful, or straight home) → OK → home.

    Every verdict this flow reaches about gataca is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far gataca got. Taps through a page object can
    still raise that page's own generic error when its button cannot be found.
    """
    device_pin = page_args.get("device_pin", "") or pin
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin, device_pin=device_pin, page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("credential_offer")
    state = {"when": "after the deeplink"}
    w = _watch(ctx, state)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected=_EXPECTED)

    failures.answer_permission(driver, ctx)
    _until_request(driver, ctx, t, state, w)

    # Share is offered, with no no-match dialog over it, only when gataca holds what the request
    # asks for.
    progress.reached(progress.PRESENT, "gataca showed Sharing requirements")
    progress.reached(progress.MATCH, "gataca offered Share for the request")
    logger.info("[verification_flow] Sharing requirements — tapping Share")
    failures.tap(ctx, "while tapping Share", lambda: CredentialOfferPage(driver, **page_args).accept())
    progress.step("tapped Share")
    state["when"] = "after Share"

    ended = _after_share(driver, ctx, t, state, w)
    logger.info(f"[verification_flow] Verification '{credential_name}' complete — {ended}")
