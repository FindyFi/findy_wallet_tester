"""What both toppan flows do while they wait, and when something goes wrong, written once.

Per-wallet on purpose: every probe and every sentence here is toppan's own. Nothing in it belongs
in base/ — base/screens.py says why a copied probe is worse than none.

- `lost`: a dead UiAutomator2 session is ours, not toppan's.
- `answer_permission`: the Android permission dialog the old flows answered after the deeplink.
- `error_probe`: toppan's error dialog, anchored on the package this flow was given.
- `first_of`: poll a few of toppan's screens until one shows.
- `dialog_failure`: toppan's error dialog as a FlowFailure, with the `[no_retry]` rule in one place.
- `raise_if_crashed`: one cheap look for Android's crash or not-responding dialog.
- `nothing_appeared`: the honest diagnosis when a wait *before* Accept or Share runs out.
"""
import logging
import re
import time
from functools import partial

from selenium.common.exceptions import WebDriverException

from base import outcome, progress
from base.android import SystemOverlay, detect_crash_or_anr, handle_permission_if_present
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from wallets.toppan.pages import error_dialog
from wallets.toppan.pages.home_page import on_screen as home_on_screen
from wallets.toppan.screens import SCREENS

logger = logging.getLogger(__name__)

# toppan's tamper check. Seen only on emulators — reports/2026-08-31_16-34-33, 2026-09-02_17-13-38
# and 2026-09-10_16-43-51, each answered by an emulator-5554/5558 (sdk_gphone64_arm64) session
# per its appium.log — and never on the phone toppan runs on. About the device, not any provider.
THREAT = "Threat detected"

# An HTTP gateway failure quoted by toppan: "Token request failed with 502 Bad Gateway+
# description=N/A" (nightly 2026-09-14 and 2026-09-16, authbound_issuer). A proxy failing in
# front of the issuer is not a verdict a rerun would only repeat.
_GATEWAY_FAILURE = re.compile(r"\b50[234] (?:Bad Gateway|Service Unavailable|Gateway Timeout)\b",
                              re.IGNORECASE)

# The re-probe after a wait runs out: one look of about this long, shared by every target.
_LATE = 1.0

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
    have. It is Android's, not toppan's, so answering it completes no stage."""
    try:
        if handle_permission_if_present(driver):
            progress.step("answered an Android permission dialog")
    except WebDriverException as exc:
        raise lost(ctx, "while looking for a permission dialog", exc) from exc


def error_probe(ctx: FlowContext):
    """`error_dialog.present` for the package this flow runs against, as a (driver, timeout)
    probe."""
    return partial(error_dialog.present, package=ctx.app_package)


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


def keeps_no_retry(said: str) -> bool:
    """Whether toppan's error dialog is treated as a verdict a rerun would only repeat.

    An assumption, not a per-message measurement: most of toppan's dialogs recur night after night
    against the same provider — "Unsupported client_id prefix" from waltid_verifier, the
    JsonDecodingException from waltid_issuer — so any readable dialog is tagged, including one seen
    for the first time. Not every one has that history: paradym_issuer's "Illegal input: Unexpected
    JSON token" appeared in 4 runs over 2 days. Two exceptions: a gateway failure, which is
    transient by nature, and a dialog that could not be read, because then nothing says what failed.
    """
    return bool(said) and not _GATEWAY_FAILURE.search(said)


def dialog_failure(driver, ctx: FlowContext, head: str) -> FlowFailure:
    """toppan's error dialog as a FlowFailure, quoting toppan. `head` says where in the flow.

    `rejected`, except toppan's tamper check, which is about the device and so `setup`.
    """
    said = error_dialog.message(driver, package=ctx.app_package)
    logger.error(f"[{ctx.flow}] toppan's error dialog for '{ctx.what}': {said or '(unreadable)'}")
    if said == THREAT:
        return FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] toppan refused to run '{ctx.what}' on this device: \"{said}\". Its "
            "tamper check has flagged only emulators so far, so this is a verdict about the "
            "device, not about the provider"
        ))
    quoted = f": \"{said}\"" if said else " (its message could not be read)"
    retry = " [no_retry]" if keeps_no_retry(said) else ""
    return FlowFailure(outcome.REJECTED, f"{head}{quoted}{retry}")


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
        f"[{ctx.flow}] toppan {'stopped responding' if anr else 'crashed'} {when} for "
        f"'{ctx.what}': Android showed its {'not-responding' if anr else 'crash'} dialog"
    ))


def nothing_appeared(driver, ctx: FlowContext, *, late, timeout: float, expected: str,
                     waited_for: str, after: str, head: str, was_in_front: bool = False) -> str:
    """A wait before Accept or Share ran out. Returns the name of a `late` screen that showed after
    all; otherwise raises the honest diagnosis.

    `late` is the wait's own (name, probe, probe timeout) list, re-probed once with about a second
    shared between its probes (`_LATE`), not a second each. Then toppan's error
    dialog (`head` says where), a crash, its wallet screen — one look, at the end, so the message
    says what was there when checked rather than how long it had been there — and finally the
    shared diagnosis, whose "no error" and "no home screen" are then both true.

    `was_in_front` says toppan already showed a screen of its own in this flow (its confirmation,
    answered with CONTINUE). Then a toppan no longer in front has *left*, and the shared diagnosis
    — "did not come to the foreground", with a check of the link's scheme — would describe a
    delivery that demonstrably worked.
    """
    try:
        for name, probe, _ in late:
            if probe(driver, _LATE / len(late)):
                progress.step(f"toppan showed {name} late")
                return name
        if error_dialog.present(driver, 1.0, package=ctx.app_package):
            progress.reached(progress.OPEN, "toppan was in front, showing its error dialog")
            raise dialog_failure(driver, ctx, head)
    except WebDriverException as exc:
        raise lost(ctx, f"after {after}", exc) from exc
    raise_if_crashed(driver, ctx, when=f"after {after}")
    try:
        on_home = home_on_screen(driver, 1.0)
    except WebDriverException as exc:
        raise lost(ctx, f"after {after}", exc) from exc
    if on_home:
        progress.reached(progress.OPEN, "toppan was in front, on its wallet screen")
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] toppan showed its wallet screen, not {waited_for}, when checked "
            f"{timeout:.0f}s after {after} for '{ctx.what}', and gave no reason"
        ))
    if was_in_front:
        try:
            front = driver.current_package
            state = _APP_STATES.get(driver.query_app_state(ctx.app_package), "unknown")
        except WebDriverException as exc:
            raise lost(ctx, f"after {after}", exc) from exc
        if front != ctx.app_package:
            # The app state says whether toppan is still alive behind `front` or gone: a reader can
            # then tell a backgrounding from a silent exit. No crash is claimed — Android showed
            # no crash dialog, or raise_if_crashed above would have said so.
            raise FlowFailure(outcome.ABSENT, (
                f"[{ctx.flow}] toppan had left the foreground when checked {timeout:.0f}s after "
                f"{after} for '{ctx.what}' ({front or 'unknown'} was in front; Android reports "
                f"toppan as {state}): neither {waited_for}, an error dialog nor its wallet screen "
                "was showing"
            ))
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected=expected, timeout=timeout)
