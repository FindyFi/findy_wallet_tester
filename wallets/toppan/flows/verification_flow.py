import logging

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.toppan.flows import _failures as failures
from wallets.toppan.pages import result_page
from wallets.toppan.pages.home_page import on_screen as home_on_screen
from wallets.toppan.pages.verification_request_page import (
    VerificationConfirmationPage,
    VerificationRequestPage,
    confirmation_on_screen,
    on_screen as request_on_screen,
)
from wallets.toppan.screens import SCREENS

logger = logging.getLogger(__name__)

_EXPECTED = "verification request"

# Web browsers a verifier's redirect_uri can open after toppan posts the presentation. A list of
# known browser packages, not a probe of what the device has installed. Only Chrome is evidenced:
# the 2026-10-27 nightly's igrant_playground_pid_mdoc and pid_sdjwt verifiers (test.log, appium.log
# 05:58:39 and 06:01:06 — current package "com.android.chrome" right after Share).
BROWSERS = (
    "com.android.chrome",
    "org.mozilla.firefox",
    "com.sec.android.app.sbrowser",
    "com.microsoft.emmx",
    "com.brave.browser",
    "com.opera.browser",
)


def _in_browser(driver, timeout: float = 0) -> bool:
    """Whether a web browser is in front — a probe for `first_of`, so a dead session reaches its
    lost-session wrap. One read, no wait: `timeout` is accepted for the probe shape only."""
    return driver.current_package in BROWSERS


def _no_retry(exc: FlowFailure) -> FlowFailure:
    """The same failure, tagged for `--rerun-except=no_retry`. The shared wording is kept."""
    text = str(exc)
    prefix = f"[{exc.category}] "
    return FlowFailure(exc.category, f"{text[len(prefix):] if text.startswith(prefix) else text}"
                                     " [no_retry]")


def _ended_early(driver, ctx: FlowContext):
    """toppan answered with its result screen instead of the request. Raises; never returns.

    "Request docType … is not available" is toppan saying it holds nothing of the type asked for:
    `no_match`. Anything else it says there — older builds said "No document shared" — is its
    verdict on the request: `rejected`. The screen is left up for the failure screenshot and dump;
    init_flow closes it through its own button in teardown, since it ignores BACK.
    """
    said = result_page.message(driver)
    logger.error(f"[verification_flow] toppan ended '{ctx.what}' before any request: "
                 f"{said or '(unreadable)'}")
    if result_page.is_no_match(said):
        # Deterministic: the 2026-09-14, -15 and -16 nightlies all ended authbound_verifier on
        # this same sentence, every rerun included. A rerun only repeats it.
        try:
            outcome.raise_no_match(SCREENS, ctx, said=said)
        except FlowFailure as exc:
            raise _no_retry(exc) from None
    if said:
        # Every rerun of this case failed at this step: authbound_verifier, nightly 2026-08-19 to
        # 2026-09-01 (reports/ from 2026-06-29). From 2026-09-02 toppan went home here instead.
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] toppan ended the verification for '{ctx.what}' before showing "
            f"the request: \"{said}\" [no_retry]"
        ))
    raise FlowFailure(outcome.REJECTED, (
        f"[verification_flow] toppan ended the verification for '{ctx.what}' on its result screen "
        "before showing the request; its message could not be read"
    ))


def _after_share(driver, ctx: FlowContext, t: float) -> str:
    """Wait for toppan's answer after Share. Returns how it ended — toppan's own success text, or
    a hand-over to a web browser; raises a FlowFailure otherwise — never the shared "nothing
    appeared", because toppan had already shown the request and taken the tap.

    The browser is the verifier's redirect_uri after direct_post, and the user decided it passes.
    It is the verifier's answer, not toppan's success screen, so it completes no stage: a step
    records it and the trail stays at `present`. A crash dialog still wins over it, and any other
    app in front is never a pass.

    Its wallet screen is no verdict while this waits: toppan shows the result screen before it
    returns there, so home is looked at once, at the end, only to say where it stopped.
    """
    watch = [("result", result_page.on_screen, 1.0), ("error", failures.error_probe(ctx), 0.5),
             ("browser", _in_browser, 0)]
    ok, said, browser = False, "", ""
    try:
        seen = failures.first_of(driver, ctx, watch, t, during="after Share")
        if not seen:
            seen = failures.first_of(driver, ctx, watch, 0, during="after Share")  # late answer
        if seen == "result":
            ok = result_page.shared(driver, 1.0)
            said = "" if ok else result_page.message(driver)
        if seen == "browser":
            browser = driver.current_package
        on_home = not seen and home_on_screen(driver, 1.0)
    except WebDriverException as exc:
        raise failures.lost(ctx, "after Share", exc) from exc

    if seen == "browser":
        failures.raise_if_crashed(driver, ctx, when="after Share")
        logger.info(f"[verification_flow] toppan handed over to {browser} after Share — the "
                    "verifier's redirect")
        progress.step(f"toppan handed over to {browser} after Share (the verifier's redirect)")
        return f"handed over to {browser}"

    if seen == "error":
        raise failures.dialog_failure(driver, ctx, (
            f"[verification_flow] toppan took the Share for '{ctx.what}' and then failed"))

    if seen == "result":
        if ok:
            progress.reached(progress.ACCEPT, "toppan showed Information Shared")
            result_page.back_home(driver)
            return "Information Shared"
        # A failure: the result screen stays up for the screenshot and dump, and init_flow closes
        # it in teardown.
        logger.error(f"[verification_flow] toppan's result after Share: {said or '(unreadable)'}")
        if said:
            # "No document shared" recurred against the same verifier every night it ran:
            # procivis_verifier, 2026-06-22 to 2026-09-03.
            raise FlowFailure(outcome.REJECTED, (
                f"[verification_flow] toppan took the Share for '{ctx.what}' and then reported: "
                f"\"{said}\" [no_retry]"
            ))
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] toppan took the Share for '{ctx.what}' and then showed its "
            "result screen without its success text; its message could not be read"
        ))

    failures.raise_if_crashed(driver, ctx, when="after Share")
    if on_home:
        raise FlowFailure(outcome.PROCESSING, (
            f"[verification_flow] toppan showed its wallet screen, not its result screen, when "
            f"checked {t:.0f}s after Share for '{ctx.what}'. Whether the presentation reached the "
            "verifier is unknown"
        ))
    raise FlowFailure(outcome.PROCESSING, (
        f"[verification_flow] toppan showed neither its result screen nor an error dialog in the "
        f"{t:.0f}s after Share for '{ctx.what}'. Whether the presentation reached the verifier "
        "is unknown"
    ))


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Toppan wallet.

    Flow:
      1. Fire deeplink
      2. "Do you want to proceed with verification?" dialog → CONTINUE
      3. "Verification Required" request screen → Share
      4. toppan's result screen: "Information Shared" passes, and so does a hand-over to a web
         browser (the verifier's redirect); anything else fails

    Every verdict this flow reaches about toppan is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far toppan got. Taps through a page object can
    still raise that page's own generic error when its button cannot be found. toppan has no PIN,
    so `pin` is unused.
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin, page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("default")
    t_share = ctx.timeout("credential_offer")

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected=_EXPECTED)

    failures.answer_permission(driver, ctx)

    head = (f"[verification_flow] toppan could not process the verification request for "
            f"'{credential_name}'")
    request = [("the verification request", request_on_screen, 1.0),
               ("its result screen", result_page.on_screen, 0.5)]
    # The confirmation has always come first, but the old flow treated it as optional, so the
    # request itself also ends this wait.
    first = [("its verification confirmation", confirmation_on_screen, 1.0)] + request
    error = [("error", failures.error_probe(ctx), 0.5)]
    seen = failures.first_of(driver, ctx, first + error, t,
                             during="while waiting for the verification confirmation")
    if seen == "error":
        progress.reached(progress.OPEN, "toppan was in front, showing its error dialog")
        raise failures.dialog_failure(driver, ctx, head)
    if not seen:
        seen = failures.nothing_appeared(driver, ctx, late=first, timeout=t, expected=_EXPECTED,
                                         waited_for="its verification confirmation",
                                         after="the deeplink", head=head)

    if seen == "its verification confirmation":
        progress.reached(progress.OPEN, "toppan asked whether to proceed with verification")
        logger.info("[verification_flow] Confirmation dialog — tapping CONTINUE")
        try:
            VerificationConfirmationPage(driver, **page_args).confirm()
        except WebDriverException as exc:
            raise failures.lost(ctx, "while tapping CONTINUE", exc) from exc
        progress.step("tapped CONTINUE")

        seen = failures.first_of(driver, ctx, request + error, t,
                                 during="while waiting for the verification request")
        if seen == "error":
            raise failures.dialog_failure(driver, ctx, head)
        if not seen:
            seen = failures.nothing_appeared(driver, ctx, late=request, timeout=t,
                                             expected=_EXPECTED,
                                             waited_for="the verification request",
                                             after="CONTINUE", head=head, was_in_front=True)

    if seen == "its result screen":
        progress.reached(progress.OPEN, "toppan was in front, on its result screen")
        _ended_early(driver, ctx)

    progress.reached(progress.PRESENT, "toppan showed the verification request")
    logger.info("[verification_flow] Verification request screen — sharing")
    try:
        VerificationRequestPage(driver, **page_args).share()
    except WebDriverException as exc:
        raise failures.lost(ctx, "while tapping Share", exc) from exc
    progress.step("tapped Share")

    ended = _after_share(driver, ctx, t_share)
    logger.info(f"[verification_flow] Verification '{credential_name}' complete — {ended}")
