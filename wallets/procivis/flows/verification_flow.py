import logging
import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.procivis.flows import _failures as failures
from wallets.procivis.pages.credential_accept_process_page import (
    INVITATION_SCREEN_ID as _invitation_id,
    present as _failure_on_screen,
)
from wallets.procivis.pages.home_page import on_screen as _home_on_screen
from wallets.procivis.pages.proof_request_sharing_page import (
    ProofRequestSharingPage,
    on_screen as sharing_on_screen,
)
from wallets.procivis.pages.proof_request_accept_process_page import (
    ProofRequestAcceptProcessPage,
    SCREEN_ID as _process_id,
)
from wallets.procivis.screens import SCREENS

logger = logging.getLogger(__name__)

# How long the share process screen is given to appear after Share. Unchanged from before the flow
# was typed; named so the messages can say it.
_SHARE_PROCESS_TIMEOUT = 5

# The activity frame every Android app has, whatever it draws inside it.
_FRAME_ID = re.compile(r"^(android:id/.*|[\w.]+:id/action_bar_root)$")


def _visible_text(driver) -> list:
    """The text of every element that has some. Each read once; a stale element is skipped."""
    try:
        elements = driver.find_elements(AppiumBy.XPATH, '//*[@text!=""]')
    except Exception:
        return []
    texts = []
    for element in elements:
        try:
            text = element.get_attribute("text")
        except WebDriverException:
            continue
        if text:
            texts.append(text)
    return texts


def _no_request(driver, ctx: FlowContext, t: float) -> None:
    """ProofRequestSharingScreen did not appear in time. Returns if it is there after all; otherwise
    raises the honest diagnosis.

    procivis answers a request it cannot open on InvitationProcessScreen — the same screen, the same
    two failure animations and the same label as issuance ("Connection failed", "No credential
    interaction found."). The failure animation decides `rejected`; visible text alone does not,
    since the wallet screen has text too.
    """
    try:
        if sharing_on_screen(driver, timeout=1):
            progress.step("procivis showed the proof request late")
            return
        if _failure_on_screen(driver, timeout=1):
            progress.reached(progress.OPEN, "procivis was in front, showing its failure screen")
            failures.rejected(driver, ctx, head=(
                f"[{ctx.flow}] procivis could not open the proof request for '{ctx.what}'"))
        if wait_present(driver, _invitation_id, timeout=1):
            # Its process screen with no failure animation: still working, and seen doing so.
            progress.reached(progress.OPEN, "procivis was in front, on its process screen")
            raise FlowFailure(outcome.PROCESSING, (
                f"[{ctx.flow}] procivis was still on its process screen after {t:.0f}s for "
                f"'{ctx.what}', with no failure animation. The proof request never rendered"
            ))
        on_home = _home_on_screen(driver, timeout=1)
    except WebDriverException as exc:
        raise failures.lost(ctx, "after the wait for the proof request", exc) from exc

    if on_home:
        # The wait never looked for home, so the shared diagnosis would say "no home screen"
        # about a wallet that was showing it. One look, at the end: nothing more is claimed.
        progress.reached(progress.OPEN, "procivis was in front, on its wallet screen")
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] procivis showed its wallet screen, not a proof request, when checked "
            f"{t:.0f}s after the deeplink for '{ctx.what}' — the deeplink was not processed or the "
            "verifier was rejected silently"
        ))

    visible = _visible_text(driver)
    if visible:
        logger.info(f"[{ctx.flow}] Visible text instead of the request: {visible}")
    try:
        # Every procivis surface was just probed, so the shared diagnosis's "no error" is true.
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="proof request",
                          timeout=t)
    except FlowFailure as exc:
        _say_what_was_there(driver, ctx, exc, visible)


def _say_what_was_there(driver, ctx: FlowContext, exc: FlowFailure, visible: list):
    """Re-raise the shared diagnosis of an unrecognised screen with what was on it. Never returns.

    The one case where what was on screen is the only clue. Added to the shared diagnosis rather
    than threaded into it; never to an `unroutable` verdict, whose [no_retry] must stay last.
    """
    if exc.category not in (outcome.ABSENT, outcome.PROCESSING):
        raise exc
    said = str(exc)[len(f"[{exc.category}] "):]
    if not visible and _nothing_readable(driver):
        # procivis 1.87.1 on 2026-10-09: React Native threw `Unable to find viewState` and
        # exposed nothing readable — only the activity frame. Not claimed to be an empty tree:
        # a WebView or unnamed images inside the frame would read the same. "A screen not
        # recognised" read like a broken locator, so say what was there, first, where the
        # tooltip shows it.
        who = ("procivis exposed" if exc.category == outcome.PROCESSING
               else "The screen in front exposed")
        tag = f"[{ctx.flow}] "
        body = said[len(tag):] if said.startswith(tag) else said
        raise FlowFailure(exc.category, (
            f"{tag}{who} nothing readable — no text, description or id beyond the activity "
            f"frame (see the screenshot). {body}"
        )) from exc
    seen = _excerpt(visible)
    if not seen:
        raise exc
    raise FlowFailure(exc.category, f"{said} Visible text: {seen}.") from exc


def _nothing_readable(driver):
    """True when the page source holds no text, no description and no id but the activity frame.

    One read-only `page_source` call. None — not a claim either way — when it cannot be read.
    """
    try:
        source = driver.page_source or ""
    except Exception:
        return None
    if not source.strip():
        return None
    if re.search(r'\s(?:text|content-desc)="[^"]+"', source):
        return False
    ids = re.findall(r'\sresource-id="([^"]+)"', source)
    return all(_FRAME_ID.match(i) for i in ids)


def _excerpt(visible, limit: int = 80) -> str:
    """A short, single-quoted excerpt of on-screen text, for a screen nothing recognised.

    Single quotes on purpose, and any double quote in the text turned into one: the app.log digest
    publishes a failure's first double-quoted span as what the *wallet* said, and an unrecognised
    screen may not be the wallet's at all.
    """
    out = ""
    for text in visible:
        piece = "'" + " ".join(str(text).split()).replace('"', "'") + "'"
        if len(out) + len(piece) + 2 > limit:
            return (out + ", …") if out else piece[:limit - 2] + "…'"
        out = f"{out}, {piece}" if out else piece
    return out


def _after_share(driver, ctx: FlowContext) -> None:
    """Wait for procivis's answer to Share. Returns only on its own success animation.

    The share process screen used to be optional: if it did not appear within 5 s the flow passed,
    whatever was on screen — a disabled Share button passed too. Now whatever is there is named.
    """
    try:
        if not wait_present(driver, _process_id, timeout=_SHARE_PROCESS_TIMEOUT):
            _share_unanswered(driver, ctx, gone=False)  # returns only if the screen came late
        logger.info(f"[{ctx.flow}] Accept process screen — waiting for completion")
        progress.step("procivis showed its share process screen")
        if ProofRequestAcceptProcessPage(driver, **ctx.page_args).wait_for_result():
            # procivis's own success animation: its evidence, not ours.
            progress.reached(progress.ACCEPT, "procivis showed its share success animation")
            return
        _share_unanswered(driver, ctx, gone=True)
    except WebDriverException as exc:
        raise failures.lost(ctx, "after Share", exc) from exc


def _share_unanswered(driver, ctx: FlowContext, *, gone: bool) -> None:
    """No success animation was seen after Share. Raises what is on screen instead.

    `gone`: the share process screen had appeared and closed before its result was seen. Without
    it, the screen had not appeared within `_SHARE_PROCESS_TIMEOUT` — and if it is there now, this
    returns so the flow waits on it. Every verdict here is raised directly: the shared `raise_for`
    describes a deeplink that was never answered, not a step after Share.
    """
    when = ("its share process screen closed before any result was seen" if gone
            else f"no share process screen appeared within {_SHARE_PROCESS_TIMEOUT}s of Share")
    if _failure_on_screen(driver, timeout=1):
        failures.rejected(driver, ctx, head=(
            f"[{ctx.flow}] procivis showed its failure screen after Share for '{ctx.what}'"))
    if not gone and wait_present(driver, _process_id, timeout=1):
        progress.step("procivis's share process screen appeared late")
        return
    if sharing_on_screen(driver, timeout=1):
        raise FlowFailure(outcome.PROCESSING, (
            f"[{ctx.flow}] procivis was still on its sharing screen after Share for '{ctx.what}' "
            f"({when}): Share was disabled or did nothing. procivis has no mapped no-match screen, "
            "so whether it holds a credential this verifier accepts is unknown"
        ))
    if _home_on_screen(driver, timeout=1):
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] procivis showed its wallet screen after Share for '{ctx.what}' ({when}) "
            "without its success animation, so nothing confirms the presentation was handed over"
        ))
    visible = _visible_text(driver)
    seen = f" Visible text: {_excerpt(visible)}." if visible else ""
    raise FlowFailure(outcome.PROCESSING, (
        f"[{ctx.flow}] After Share for '{ctx.what}', {when}, and procivis showed neither a success "
        f"animation, its failure screen, its sharing screen nor its wallet screen.{seen}"
    ))


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Procivis wallet.

    Every verdict this flow reaches about procivis is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far procivis got. Taps through a page object
    (Share) can still raise that page's own generic error when its button cannot be found.

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
