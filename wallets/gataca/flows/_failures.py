"""What gataca's flows do while they wait, and when something goes wrong, written once.

Per-wallet on purpose: every probe and every sentence here is gataca's own. Nothing in it belongs
in base/ — base/screens.py says why a copied probe is worse than none. The generic helpers keep
paradym's names and signatures; the rest is gataca's.

- `lost`: a dead UiAutomator2 session is ours, not gataca's.
- `answer_permission`: the Android permission dialog the old flows answered after the deeplink.
- `tap`: a page object's tap, with a driver error as a lost session.
- `first_of`: poll a few of gataca's screens until one shows.
- `raise_if_crashed`: one cheap look for Android's crash or not-responding dialog.
- `late_look`: one more look at a wait's targets, about a second shared between them.
- `answer_auth`: the system auth sheet gataca raises several times in one flow, with the three
  ways it can go wrong typed (no PIN field, a sheet that stayed up, a fingerprint that never took).
- `error_failure`, `rejected_failure`: gataca's error screen and its verifier's "Rejected" dialog
  as FlowFailures, with the `[no_retry]` rule in one place.
- `nothing_appeared`: the honest diagnosis when a wait before Share runs out.
- `guarded`: init_flow's and setup_flow's calls, typed.
"""
import logging
import time
from contextlib import contextmanager

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from base import outcome, progress
from base.android import (
    BIOMETRIC_PROMPT,
    PinFieldMissing,
    SystemOverlay,
    authenticate_device,
    detect_crash_or_anr,
    handle_permission_if_present,
)
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from base.utils import wait_present
from wallets.gataca.pages import credential_offer_page, error_page, success_page
from wallets.gataca.pages.home_page import on_screen as home_on_screen
from wallets.gataca.screens import SCREENS

logger = logging.getLogger(__name__)

# The re-probe after a wait runs out: one look of about this long, shared by every target.
_LATE = 1.0

# How many simulated fingerprints in a row may leave the sheet up before the emulator is blamed. The
# sheet is re-probed after each; an unenrolled fingerprint is accepted by the injection and
# ignored by Android, so the sheet never closes and every wait would otherwise run to its 30 s.
_FINGERPRINT_TRIES = 3

# How long to wait for the sheet to go after answering it. A real sheet clears in about 1.2 s; the
# default of 10 s per try would make three unregistered fingerprints cost as much as the wait.
_DISMISS = 5

# How many times one flow may answer the system auth sheet. Issuance legitimately meets it three
# times (before the offer, after Share, after Login Successful); more than this is gataca asking
# again instead of progressing.
_MAX_AUTH_ANSWERS = 6

# `driver.query_app_state` codes (Appium's ApplicationState).
_APP_STATES = {0: "not installed", 1: "not running", 2: "running in the background, suspended",
               3: "running in the background", 4: "running in the foreground"}


def lost(ctx: FlowContext, during: str, exc) -> FlowFailure:
    """Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record."""
    return FlowFailure(outcome.LOST_SESSION, (
        f"[{ctx.flow}] Appium/UiAutomator2 connection lost {during} for '{ctx.what}' "
        f"(app may have crashed): {getattr(exc, 'msg', None) or exc}"
    ))


def answer_permission(driver, ctx: FlowContext) -> None:
    """Answer an Android permission dialog if one is up after the deeplink, as the flows always
    have. It is Android's, not gataca's, so answering it completes no stage."""
    try:
        if handle_permission_if_present(driver):
            progress.step("answered an Android permission dialog")
    except WebDriverException as exc:
        raise lost(ctx, "while looking for a permission dialog", exc) from exc


def tap(ctx: FlowContext, during: str, action) -> None:
    """Run a page object's tap. A driver error in it is a dead session, as everywhere in these
    flows; the page's own generic "not clickable" error is left to escape as it always has."""
    try:
        action()
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc


def first_of(driver, ctx: FlowContext, screens, timeout: float, *, during: str) -> str:
    """Poll `screens` — (name, probe, probe timeout) in order — until one shows. Returns its name,
    or "" once `timeout` has run out with none of them seen. Every probe runs at least once."""
    end = time.monotonic() + timeout
    try:
        while True:
            for name, probe, wait in screens:
                if probe(driver, wait):
                    return name
            if time.monotonic() >= end:
                return ""
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc


def raise_if_crashed(driver, ctx: FlowContext, when: str) -> None:
    """One look for Android's crash or not-responding dialog before a negative verdict.

    A dead session during the look is `lost_session`, as in `base.outcome.wait_for`: otherwise the
    caller's next verdict would be written about a session that can no longer see anything. Any
    other failure of the look claims nothing and is only logged.
    """
    try:
        overlay = detect_crash_or_anr(driver)
    except WebDriverException as exc:
        raise lost(ctx, f"while looking for a crash dialog {when}", exc) from exc
    except Exception as exc:
        logger.warning(f"[{ctx.flow}] Could not look for a crash dialog: {exc}")
        return
    if overlay is None:
        return
    anr = overlay is SystemOverlay.ANR
    raise FlowFailure(outcome.CRASHED, (
        f"[{ctx.flow}] gataca {'stopped responding' if anr else 'crashed'} {when} for "
        f"'{ctx.what}': Android showed its {'not-responding' if anr else 'crash'} dialog"
    ))


def late_look(driver, ctx: FlowContext, screens, *, during: str) -> str:
    """One more look at `screens` once a wait has run out, about a second (`_LATE`) shared between
    them rather than a second each. Returns the name of the first one showing, or ""."""
    try:
        for name, probe, _ in screens:
            if probe(driver, _LATE / len(screens)):
                return name
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    return ""


# --- the system auth sheet -----------------------------------------------------------------------

def pin_field_failure(ctx: FlowContext, when: str) -> FlowFailure:
    """The phone's sheet has no PIN to type: gataca removed that option in favour of the
    fingerprint, which no framework can give a physical phone. Ours to have prepared for."""
    return FlowFailure(outcome.SETUP, (
        f"[{ctx.flow}] The phone's system sheet offers only a fingerprint, which cannot be "
        f"automated here (PIN fallback missing), so the device authentication gataca asked for "
        f"{when} for '{ctx.what}' could not be answered"
    ))


def _stayed_failure(ctx: FlowContext, when: str, exc) -> FlowFailure:
    said = " ".join(str(getattr(exc, "msg", None) or exc).split())
    return FlowFailure(outcome.SETUP, (
        f"[{ctx.flow}] The system auth sheet gataca raised {when} for '{ctx.what}' did not close "
        f"after the device PIN was accepted (Android may be asking to enrol a fingerprint, which "
        f"needs a real finger): {said[:200]}"
    ))


def answer_auth(driver, ctx: FlowContext, when: str, *, detect_timeout: float = 0.4) -> bool:
    """Answer the system auth sheet if one is up: a fingerprint on an emulator, the device PIN on a
    phone (`authenticate_device`). Returns True if a sheet was answered.

    The three ways it can go wrong are ours, not gataca's, and are typed `setup`:
    - a fingerprint-only sheet on a phone (`PinFieldMissing`): nothing to type;
    - the PIN accepted but the sheet staying (selenium's `TimeoutException`, a subclass of
      `WebDriverException`, so it is caught first), usually Android's enrolment wizard;
    - on an emulator, a sheet still up after `_FINGERPRINT_TRIES` fingerprints in a row: no
      enrolled fingerprint, or the injection failed. `authenticate_device` reports that as handled.

    More than `_MAX_AUTH_ANSWERS` answers in one flow is gataca asking again: `prompt_loop`.
    """
    try:
        handled = authenticate_device(
            driver, ctx.device_pin, detect_timeout=detect_timeout, dismiss_timeout=_DISMISS)
    except PinFieldMissing as exc:
        raise pin_field_failure(ctx, when) from exc
    except TimeoutException as exc:
        raise _stayed_failure(ctx, when, exc) from exc
    except WebDriverException as exc:
        raise lost(ctx, f"while answering the system auth sheet {when}", exc) from exc
    if not handled:
        return False

    answers = ctx.prompt_budget.get("auth_answers", 0) + 1
    ctx.prompt_budget["auth_answers"] = answers
    progress.step(f"answered the system auth sheet {when} ({answers})")
    if answers > _MAX_AUTH_ANSWERS:
        raise FlowFailure(outcome.PROMPT_LOOP, (
            f"[{ctx.flow}] gataca raised the system auth sheet {answers} times {when} for "
            f"'{ctx.what}', and every one was answered, instead of progressing"
        ))

    try:
        still_up = wait_present(driver, BIOMETRIC_PROMPT, timeout=0.5)
    except WebDriverException as exc:
        raise lost(ctx, f"while re-checking the system auth sheet {when}", exc) from exc
    if not still_up:
        ctx.prompt_budget.pop("auth_tries", None)
        ctx.prompt_budget.pop("auth_since", None)
        return True
    tries = ctx.prompt_budget.get("auth_tries", 0) + 1
    ctx.prompt_budget["auth_tries"] = tries
    since = ctx.prompt_budget.setdefault("auth_since", int(time.monotonic()))
    if tries >= _FINGERPRINT_TRIES:
        raise FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] The system fingerprint sheet stayed up after {tries} simulated "
            f"fingerprints in {time.monotonic() - since:.0f}s {when} for '{ctx.what}'; the "
            "emulator has no enrolled fingerprint, or the injection failed"
        ))
    return True


# --- gataca's verdicts ---------------------------------------------------------------------------

def error_failure(driver, ctx: FlowContext, head: str) -> FlowFailure:
    """gataca's error screen as a `rejected` FlowFailure, quoting what it said. `head` says where
    in the flow. The screen is left up: the failure screenshot and dump are taken after the flow
    raises, and init_flow taps Go back in teardown.

    Every readable error is `[no_retry]`: hovi_issuer's text repeated on 3 of 3 attempts, and the
    others are the issuer's configuration. One whose text could not be read is not, because then
    nothing says what failed."""
    said = error_page.message(driver)
    logger.error(f"[{ctx.flow}] gataca's error screen for '{ctx.what}': "
                 f"{said or '(no text read)'}")
    quoted = f": \"{said}\"" if said else " (its text could not be read)"
    return FlowFailure(outcome.REJECTED, f"{head}{quoted}{' [no_retry]' if said else ''}")


def rejected_failure(driver, ctx: FlowContext) -> FlowFailure:
    """The verifier's "Rejected" dialog (hovi_verifier: "Rejected", "core-agent.hovi.id", OK) as a
    `rejected` FlowFailure, tagged `[no_retry]`: the verifier gave the same answer on all three
    attempts in every run (2026-11-19), so a rerun only repeats it. The dialog is left up. Keeps
    the words "was rejected" the report's old-runs pattern reads."""
    host = success_page.rejected_detail(driver)
    logger.error(f"[{ctx.flow}] gataca's Rejected dialog for '{ctx.what}': {host or '(no detail)'}")
    by = f" by {host}" if host else ""
    return FlowFailure(outcome.REJECTED, (
        f"[{ctx.flow}] Credential verification for '{ctx.what}' was rejected{by}: "
        f"\"Rejected\" [no_retry]"
    ))


def no_match_failure(driver, ctx: FlowContext, *, issuance: bool = False) -> FlowFailure:
    """gataca's "giving consent is not possible" dialog as `no_match`, tagged `[no_retry]`: the
    wallet holds nothing that satisfies the request, which no rerun changes. The dialog is left
    up for the failure screenshot and dump. Worded for what was asked: a verifier's request, or
    the sharing requirements an issuer sets before it will issue."""
    said = credential_offer_page.no_match_text(driver)
    if issuance:
        tail = ("It holds no credential that the issuer's sharing requirements ask for before "
                "issuing")
        head = f"[{ctx.flow}] gataca showed the sharing requirements for '{ctx.what}' and answered"
    else:
        tail = "It holds nothing that satisfies what this verifier asked for"
        head = f"[{ctx.flow}] gataca received the request for '{ctx.what}' and answered"
    return FlowFailure(outcome.NO_MATCH, f"{head} \"{said}\". {tail} [no_retry]")


def nothing_appeared(driver, ctx: FlowContext, *, late, timeout: float, expected: str,
                     waited_for: str, after: str, head: str, was_in_front: bool = False,
                     fetching=None) -> str:
    """A wait before Share ran out. Returns the name of a `late` screen that showed after all;
    otherwise raises the honest diagnosis.

    `late` is the wait's own (name, probe, probe timeout) list, re-probed once (`late_look`).
    Then gataca's error screen and its
    Rejected dialog (`head` says where), a crash, its "Sending your consent" overlay when the
    caller passes a `fetching` probe, its home screen — one look each, at the end, so the message
    says what was there when checked — and finally the shared diagnosis.

    A bare home is `dismissed` only after `late` has had its look: "Verification Complete" and
    "Credentials Shared" overlay home, whose heading stays in the tree beneath them.
    """
    during = f"after {after}"
    seen = late_look(driver, ctx, list(late), during=during)
    if seen:
        progress.step(f"gataca showed {seen} late")
        return seen
    try:
        if error_page.present(driver, 1.0):
            progress.reached(progress.OPEN, "gataca was in front, showing its error screen")
            raise error_failure(driver, ctx, head)
        if success_page.on_rejected_screen(driver, 1.0):
            progress.reached(progress.OPEN, "gataca was in front, showing its Rejected dialog")
            raise rejected_failure(driver, ctx)
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    raise_if_crashed(driver, ctx, when=during)
    try:
        still_fetching = bool(fetching) and fetching(driver, 1.0)
        on_home = not still_fetching and home_on_screen(driver, 1.0)
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    if still_fetching:
        progress.reached(progress.OPEN, "gataca was in front, sending its consent")
        raise FlowFailure(outcome.PROCESSING, (
            f"[{ctx.flow}] gataca was still on 'Sending your consent' when checked "
            f"{timeout:.0f}s after {after} for '{ctx.what}', and had not shown {waited_for}"
        ))
    if on_home:
        progress.reached(progress.OPEN, "gataca was in front, on its home screen")
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] gataca showed its home screen, not {waited_for}, when checked "
            f"{timeout:.0f}s after {after} for '{ctx.what}', and gave no reason"
        ))
    if was_in_front:
        try:
            front = driver.current_package
            state = _APP_STATES.get(driver.query_app_state(ctx.app_package), "unknown")
        except WebDriverException as exc:
            raise lost(ctx, during, exc) from exc
        if front != ctx.app_package:
            raise FlowFailure(outcome.ABSENT, (
                f"[{ctx.flow}] gataca had left the foreground when checked {timeout:.0f}s after "
                f"{after} for '{ctx.what}' ({front or 'unknown'} was in front; Android reports "
                f"gataca as {state}): neither {waited_for}, its error screen nor its home screen "
                "was showing"
            ))
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected=expected, timeout=timeout)


# --- init_flow and setup_flow --------------------------------------------------------------------

@contextmanager
def guarded(ctx: FlowContext, during: str):
    """Type whatever a block of init_flow or setup_flow calls can raise, so none of it reaches the
    report as a bare exception.

    A FlowFailure passes through. A missing PIN field on the sheet, a sheet that stayed after the
    PIN, a wait that timed out, an element that is not where it was, or any other exception one of
    the page objects raised (`BasePage.click` raises a bare Exception when a button is not
    clickable) are all `setup`: the wallet could not be put in the state the tests need. Any other
    driver error is a dead session, `lost_session`.
    """
    try:
        yield
    except FlowFailure:
        raise
    except PinFieldMissing as exc:
        raise pin_field_failure(ctx, during) from exc
    except TimeoutException as exc:
        said = " ".join(str(getattr(exc, "msg", None) or exc).split())
        raise FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] Timed out {during}{': ' + said[:200] if said else ''}"
        )) from exc
    except (NoSuchElementException, StaleElementReferenceException) as exc:
        raise FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] A screen element was not where it was expected {during}: "
            f"{' '.join(str(getattr(exc, 'msg', None) or exc).split())[:200]}"
        )) from exc
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    except Exception as exc:
        raise FlowFailure(outcome.SETUP, f"[{ctx.flow}] {' '.join(str(exc).split())}") from exc
