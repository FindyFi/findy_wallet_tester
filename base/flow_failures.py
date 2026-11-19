"""What every wallet's flows do when something goes wrong, once the wallet itself is not involved.

These helpers deal with the driver, the `FlowContext` and Android's own dialogs, never with a
wallet's screens: a dead UiAutomator2 session, a crash or not-responding dialog, an Android
permission dialog, and the polling loops that look at a few probes in order. They name the wallet
only through `ctx.wallet`, so one body serves all of them.

What belongs here: anything that would read the same in every wallet's `flows/_failures.py` apart
from the wallet's name. What stays in a wallet's `flows/_failures.py`: its probes, its error text,
its `[no_retry]` rules, and every diagnosis that names one of its own screens
(`nothing_appeared`, `error_failure` and the like). base/screens.py says why a copied probe is
worse than none.

Tests patch the probe names here (`base.flow_failures.detect_crash_or_anr`,
`base.flow_failures.handle_permission_if_present`), never a wallet module's: patching a wallet's
`failures.lost` does not reach the helpers below. `time` is imported as a module so a test can
patch `time.monotonic`.
"""
import logging
import time
from typing import Optional

from selenium.common.exceptions import WebDriverException

from base import outcome, progress
from base.android import SystemOverlay, detect_crash_or_anr, handle_permission_if_present
from base.flow_context import FlowContext
from base.outcome import FlowFailure

logger = logging.getLogger(__name__)

# The re-probe after a wait runs out: one look of about this long, shared by every target.
LATE = 1.0

# `driver.query_app_state` codes (Appium's ApplicationState).
APP_STATES = {0: "not installed", 1: "not running", 2: "running in the background, suspended",
              3: "running in the background", 4: "running in the foreground"}


def lost_session(flow: str, during: str, said, what: Optional[str] = None) -> FlowFailure:
    """Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record.

    `said` is the text already chosen for the cause. With `what` None the credential or case is
    left out of the sentence (the flows that have no context to take it from).
    """
    for_what = "" if what is None else f" for '{what}'"
    return FlowFailure(outcome.LOST_SESSION, (
        f"[{flow}] Appium/UiAutomator2 connection lost {during}{for_what} "
        f"(app may have crashed): {said}"
    ))


def lost(ctx: FlowContext, during: str, exc) -> FlowFailure:
    """Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record."""
    return lost_session(ctx.flow, during, getattr(exc, 'msg', None) or exc, ctx.what)


def answer_permission(driver, ctx: FlowContext) -> None:
    """Answer an Android permission dialog if one is up after the deeplink, as the flows always
    have. It is Android's, not the wallet's, so answering it completes no stage."""
    try:
        if handle_permission_if_present(driver):
            progress.step("answered an Android permission dialog")
    except WebDriverException as exc:
        raise lost(ctx, "while looking for a permission dialog", exc) from exc


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


def raise_if_crashed(driver, ctx: FlowContext, when: str, *,
                     dead_session_is_lost: bool = True) -> None:
    """One look for Android's crash or not-responding dialog before a negative verdict.

    A dead session during the look is `lost_session`, as in `base.outcome.wait_for`: otherwise the
    caller's next verdict would be written about a session that can no longer see anything. Any
    other failure of the look claims nothing and is only logged. With `dead_session_is_lost`
    False a dead session is only logged too, for a caller that must not turn it into a verdict.
    """
    try:
        overlay = detect_crash_or_anr(driver)
    except WebDriverException as exc:
        if not dead_session_is_lost:
            logger.warning(f"[{ctx.flow}] Could not look for a crash dialog: {exc}")
            return
        raise lost(ctx, f"while looking for a crash dialog {when}", exc) from exc
    except Exception as exc:
        logger.warning(f"[{ctx.flow}] Could not look for a crash dialog: {exc}")
        return
    if overlay is None:
        return
    anr = overlay is SystemOverlay.ANR
    raise FlowFailure(outcome.CRASHED, (
        f"[{ctx.flow}] {ctx.wallet} {'stopped responding' if anr else 'crashed'} {when} for "
        f"'{ctx.what}': Android showed its {'not-responding' if anr else 'crash'} dialog"
    ))


def late_look(driver, ctx: FlowContext, screens, *, during: str) -> str:
    """One more look at `screens` once a wait has run out, about a second (`LATE`) shared between
    them rather than a second each. Returns the name of the first one showing, or ""."""
    try:
        for name, probe, _ in screens:
            if probe(driver, LATE / len(screens)):
                return name
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    return ""
