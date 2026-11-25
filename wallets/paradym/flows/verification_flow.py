import logging

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.paradym.flows import _failures as failures
from wallets.paradym.pages import error_page, result_page
from wallets.paradym.pages.issuer_consent_page import IssuerConsentPage
from wallets.paradym.pages.issuer_consent_page import on_screen as consent_on_screen
from wallets.paradym.pages.pin_page import on_screen as pin_on_screen
from wallets.paradym.pages.verification_request_page import (
    NO_MATCH_TEXT,
    VerificationRequestPage,
    no_match as no_match_on_screen,
    on_screen as request_on_screen,
)
from wallets.paradym.screens import SCREENS

logger = logging.getLogger(__name__)

_EXPECTED = "verification request"

# Web browsers a verifier's redirect_uri can open after paradym posts the presentation. A list of
# known browser packages, not a probe of what the device has installed. Only Chrome is evidenced:
# the nightly 2026-10-27 igrant_playground_pid_mdoc_verifier (appium.log 03:31:56, current package
# "com.android.chrome" after the PIN that followed Share), which the old flow passed by returning
# without looking — as it did igrant_playground_pid_sdjwt_verifier on 2026-10-26 and -27.
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


def _until_request(driver, ctx: FlowContext, t: float) -> None:
    """Answer paradym's screens between the deeplink and the request, in whatever order they come.
    Returns once the request offers Share; raises a FlowFailure otherwise.

    Seen on passing runs (nightly 2026-10-27, test.log): PIN, the verifier consent ("Yes,
    continue"), then the request. Each is answered at most once — one still up when a wait runs
    out is paradym asking again, not an unknown screen — and every wait for the next one gets the
    full `t`.
    """
    head = (f"[verification_flow] paradym could not process the verification request for "
            f"'{ctx.what}'")
    verdicts = [("error", error_page.present, 0.5), ("no match", no_match_on_screen, 0.5)]
    steps = [("its PIN screen", pin_on_screen, 0.5),
             ("its verifier consent", consent_on_screen, 0.5)]
    request = [("the verification request", request_on_screen, 1.0)]
    answered = []
    after, in_front = "the deeplink", False

    while True:
        seen = failures.first_of(driver, ctx, verdicts + steps + request, t,
                                 during="while waiting for the verification request")
        if not seen:
            seen = failures.nothing_appeared(
                driver, ctx, late=[verdicts[1]] + steps + request, timeout=t,
                expected=_EXPECTED, waited_for="the verification request", after=after,
                head=head, was_in_front=in_front, answered=answered)
        if seen == "error":
            progress.reached(progress.OPEN, "paradym was in front, showing its error screen")
            raise failures.error_failure(driver, ctx, head)
        if seen == "no match":
            # The review screen is the request, shown; what it lacks is a card that answers it.
            # Every rerun repeated it: procivis_verifier failed all three attempts on each of 39
            # nights, 2026-08-19 to 2026-10-14, and paradym_verifier on 2026-09-15. The screen is
            # left up for the failure screenshot and dump; init_flow closes it in teardown.
            progress.reached(progress.PRESENT, "paradym showed the request under UNAVAILABLE CARDS")
            try:
                outcome.raise_no_match(SCREENS, ctx, said=NO_MATCH_TEXT)
            except FlowFailure as exc:
                raise _no_retry(exc) from None
        if seen == "the verification request":
            return

        answered += [s for s in steps if s[0] == seen]
        steps = [s for s in steps if s[0] != seen]
        in_front = True
        if seen == "its PIN screen":
            failures.enter_pin(driver, ctx, "after the deeplink")
            after = "the PIN"
            continue
        progress.reached(progress.OPEN, "paradym asked to confirm the verifier interaction")
        try:
            IssuerConsentPage(driver, **ctx.page_args).confirm()
        except WebDriverException as exc:
            raise failures.lost(ctx, "while confirming the verifier consent", exc) from exc
        logger.info("[verification_flow] Verifier consent confirmed")
        progress.step("confirmed the verifier consent")
        after = "the consent"


def _after_share(driver, ctx: FlowContext, t: float) -> str:
    """Wait for paradym's answer after Share, entering its PIN when it asks. Returns how it ended
    — paradym's Go to wallet, or a hand-over to a web browser; raises a FlowFailure otherwise —
    never the shared "nothing appeared", because paradym had already shown the request and taken
    the tap.

    The browser is the verifier's redirect_uri after direct_post, which the old flow passed and
    which toppan passes by the user's decision. The nightly 2026-10-27 recordings of both igrant
    verifiers show Chrome on the verifier's own "Verification Successful" page. It is the
    verifier's answer, not paradym's success screen, so it completes no stage: a step records it
    and the trail stays at `match`. A crash dialog still wins over it, and any other app in front
    is never a pass.
    """
    head = f"[verification_flow] paradym took the Share for '{ctx.what}' and then failed"
    pin = [("its PIN screen", pin_on_screen, 0.5)]
    rest = [("success", result_page.go_to_wallet_on_screen, 1.0), ("browser", _in_browser, 0)]
    error = [("error", error_page.present, 0.5)]
    browser = ""
    try:
        seen = failures.first_of(driver, ctx, error + pin + rest, t, during="after Share")
        if seen == "its PIN screen":
            failures.enter_pin(driver, ctx, "after Share")
            seen = failures.first_of(driver, ctx, error + rest, t, during="after the PIN")
        if not seen:
            seen = failures.late_look(driver, ctx, error + rest, during="after Share")
        if seen == "browser":
            browser = driver.current_package
    except WebDriverException as exc:
        raise failures.lost(ctx, "after Share", exc) from exc

    if failures.finish(driver, ctx, seen, after="Share", head=head):
        return "Go to wallet"

    failures.raise_if_crashed(driver, ctx, when="after Share")
    if seen == "browser":
        logger.info(f"[verification_flow] paradym handed over to {browser} after Share — the "
                    "verifier's redirect")
        progress.step(f"paradym handed over to {browser} after Share (the verifier's redirect)")
        return f"handed over to {browser}"
    raise FlowFailure(outcome.PROCESSING, (
        f"[verification_flow] paradym showed neither its Go to wallet nor its error screen in the "
        f"{t:.0f}s after Share for '{ctx.what}'. Whether the presentation reached the verifier "
        "is unknown"
    ))


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the wallet.

    Every verdict this flow reaches about paradym is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far paradym got. Taps through a page object can
    still raise that page's own generic error when its button cannot be found.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package to handle the deeplink (e.g. "id.paradym.wallet")
        pin:             PIN to unlock the app, and to confirm the presentation after Share
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin, page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("credential_offer")

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected=_EXPECTED)

    failures.answer_permission(driver, ctx)
    _until_request(driver, ctx, t)

    # Share is offered only when paradym holds what the request asks for: the no-match screen in
    # the dumps has Close in its place.
    progress.reached(progress.PRESENT, "paradym showed the verification request")
    progress.reached(progress.MATCH, "paradym offered Share for the request")
    logger.info("[verification_flow] Review request screen — sharing")
    try:
        VerificationRequestPage(driver, **page_args).share()
    except WebDriverException as exc:
        raise failures.lost(ctx, "while tapping Share", exc) from exc
    progress.step("tapped Share")

    ended = _after_share(driver, ctx, t)
    logger.info(f"[verification_flow] Verification '{credential_name}' complete — {ended}")
