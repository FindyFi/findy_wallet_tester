"""What both paradym flows do while they wait, and when something goes wrong, written once.

Per-wallet on purpose: every probe and every sentence here is paradym's own. Nothing in it belongs
in base/ — base/screens.py says why a copied probe is worse than none.

- `lost`: a dead UiAutomator2 session is ours, not paradym's.
- `answer_permission`: the Android permission dialog the old flows answered after the deeplink.
- `first_of`: poll a few of paradym's screens until one shows.
- `raise_if_crashed`: one cheap look for Android's crash or not-responding dialog.
- `late_look`: one more look at a wait's targets, about a second shared between them.
- `error_failure`: paradym's error screen as a FlowFailure, with the `[no_retry]` rule in one place.
- `enter_pin`: paradym's PIN screen, which both flows meet after the deeplink.
- `finish`: the end of a wait after Accept or Share — paradym's error, or its Go to wallet with no
  error heading beside it.
- `nothing_appeared`: the honest diagnosis when a wait *before* Accept or Share runs out.
"""
import logging
import re
import time

from selenium.common.exceptions import WebDriverException

from base import outcome, progress
from base.android import SystemOverlay, detect_crash_or_anr, handle_permission_if_present
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from wallets.paradym.pages import error_page, result_page
from wallets.paradym.pages.home_page import on_screen as home_on_screen
from wallets.paradym.pages.pin_page import PinPage
from wallets.paradym.screens import SCREENS

logger = logging.getLogger(__name__)

# A gateway in front of the counterparty failing, in the two ways paradym words an HTTP status —
# "resulted in an unsuccessful response with status '400'" (procivis_issuer) and "Received token
# error response with status 400" (authbound_issuer) — or as the status line itself. No paradym
# Reason in reports/ or the nightly clone has carried a 5xx yet; this is the transient shape, not
# a measured one. Like the two below, it matches only text in the development-mode detail (see
# `keeps_no_retry`).
_GATEWAY_FAILURE = re.compile(
    r"\bstatus '?50[234]\b|\b50[234] (?:Bad Gateway|Service Unavailable|Gateway Timeout)\b",
    re.IGNORECASE)

# An offer the issuer no longer has (development-mode detail only): "Fetching credential offer
# from '…' resulted in an unsuccessful response with status '404'". hovi_issuer,
# reports/2026-09-03_11-49-09 and 2026-09-03_11-52-14, and the next run against the same issuer
# (11-54-16) passed. Every attempt asks the provider for a fresh offer, so a rerun is worth having.
_CONSUMED_OFFER = re.compile(r"\bFetching credential offer\b.*\bstatus '?404\b",
                             re.IGNORECASE)

# paradym's request never reaching the counterparty (development-mode detail only). Seen only on
# 2026-10-29, when every cell against all five pensiondemo providers — paradym's own issuer
# included — failed this way, in the device run reports/2026-10-29_10-36-12 and the nightly
# PUBLISH 2026-10-29_22-00-03, and the same providers passed again from 2026-10-30. The
# devices' network, not the providers. Two phrasings, each from those dumps:
#   issuance:     "Unknown error occurred during fetch to '…'" with "Cause: fetch failed:
#                 java.net.UnknownHostException: Unable to resolve host …"
#   verification: "Fetching request_object from request_uri '…' failed" (invalid_request_uri)
# No dump or log holds "Network request failed", a timeout, or any other transport wording, so
# none is matched.
_TRANSPORT_FAILURE = re.compile(
    r"\bUnknown error occurred during fetch\b|\bjava\.net\.UnknownHostException\b"
    r"|\bFetching request_object from request_uri\b.*\bfailed\b",
    re.IGNORECASE)

# Whether the missing-detail warning in `error_failure` has been logged: once per process, since
# every error read on a device with Development Mode off would repeat the same sentence.
_warned_no_dev_mode = False

# The re-probe after a wait runs out: one look of about this long, shared by every target.
_LATE = 1.0

# One more look for paradym's error heading once "Go to wallet" is up. Its error screen carries
# the same button (see pages/result_page.py), so the button alone is not success; the heading is
# probed first on every pass, but the screen can render between the two probes.
_LAST_ERROR_LOOK = 1.0

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
    have. It is Android's, not paradym's, so answering it completes no stage."""
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
        f"[{ctx.flow}] paradym {'stopped responding' if anr else 'crashed'} {when} for "
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


def keeps_no_retry(said: str, reason: str) -> bool:
    """Whether paradym's error screen is treated as a verdict a rerun would only repeat.

    Mostly yes. Of the 52 paradym runs in the nightly clone (2026-08-19 to 2026-10-27), the same
    Reason ended every run that reached it against the same pensiondemo provider: waltid_issuer
    in 40 runs, procivis_issuer 50, authbound_issuer 26 (until it began passing on 2026-09-17),
    authbound_verifier 25 and waltid_verifier 40. Not always: paradym_issuer's "Error while
    retrieving credentials" (an invalid VCT type metadata) ended one run only, 2026-09-15, and the
    tag held back the reruns that would have said whether it repeated that night.

    `said` is paradym's verdict, `reason` the whole Reason as read — not the detail cut down for
    the message, so a status at its end still counts.

    Four exceptions: an offer the issuer no longer has (`_CONSUMED_OFFER`, which a rerun with a
    fresh offer has cured), a request that never reached the counterparty (`_TRANSPORT_FAILURE`,
    the devices' network on 2026-10-29), a gateway failure, which is transient by nature, and a
    Reason that could not be read, because then nothing says what failed.

    The first three can only fire on text inside paradym's "Development mode error:" detail, which
    paradym shows only with Development Mode on. A freshly installed paradym has it off
    (reports/2026-11-05_17-08-31, paradym 1.23.0: none of its 4 dumps has the detail); there only
    the short verdict is read, so every readable error is `[no_retry]` — a network outage
    included. The phone has it on because the nightly runs main, whose paradym conftest still
    turns it on; this branch removed that in 5f37280. `error_failure` warns once when it is off.
    """
    text = " ".join(reason.split())
    return (bool(said) and not _GATEWAY_FAILURE.search(text)
            and not _CONSUMED_OFFER.search(text) and not _TRANSPORT_FAILURE.search(text))


def error_failure(driver, ctx: FlowContext, head: str) -> FlowFailure:
    """paradym's error screen as a `rejected` FlowFailure, quoting its Reason. `head` says where
    in the flow. The screen is left up: the failure screenshot and dump are taken after the flow
    raises, and init_flow taps its Go to wallet in teardown."""
    raw = error_page.raw_reason(driver)
    said, detail = error_page.split_reason(raw)
    logger.error(f"[{ctx.flow}] paradym's error screen for '{ctx.what}': "
                 f"{' '.join(raw.split()) or '(no Reason read)'}")
    global _warned_no_dev_mode
    if said and not detail and not _warned_no_dev_mode:
        _warned_no_dev_mode = True
        logger.warning(
            "[paradym] No development-mode detail under paradym's Reason — Development Mode looks "
            "off on this device, so a network failure, an expired offer or a gateway error cannot "
            "be told from a verdict and will not be retried")
    quoted = f": \"{said}\"" if said else " (its Reason could not be read)"
    more = f". paradym's development-mode detail: {detail}" if detail else ""
    retry = " [no_retry]" if keeps_no_retry(said, raw) else ""
    return FlowFailure(outcome.REJECTED, f"{head}{quoted}{more}{retry}")


def enter_pin(driver, ctx: FlowContext, when: str) -> None:
    """Unlock paradym's PIN screen. A missing PIN is our configuration, not the wallet's
    behaviour: `setup`. The screen is paradym's own, so it was in front."""
    progress.reached(progress.OPEN, f"paradym asked for its PIN {when}")
    if not ctx.pin:
        raise FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] App showed PIN screen {when} — pass pin= to {ctx.flow}.run()"
        ))
    logger.info(f"[{ctx.flow}] PIN screen {when} — entering PIN")
    try:
        PinPage(driver, **ctx.page_args).enter_pin(ctx.pin)
    except WebDriverException as exc:
        raise lost(ctx, "while entering the PIN", exc) from exc
    progress.step(f"entered the wallet PIN {when}")


def finish(driver, ctx: FlowContext, seen: str, *, after: str, head: str) -> bool:
    """Settle what a wait after Accept or Share saw. `seen` is "error", "success" (Go to wallet) or
    anything else.

    Raises paradym's error as `rejected` (`head` says where) — including the one found by a last
    look when Go to wallet was up. On a success completes `accept`, taps Go to wallet and returns
    True. Returns False for anything else, which the caller diagnoses.
    """
    try:
        if seen == "success" and error_page.present(driver, _LAST_ERROR_LOOK):
            seen = "error"
    except WebDriverException as exc:
        raise lost(ctx, f"after {after}", exc) from exc
    if seen == "error":
        raise error_failure(driver, ctx, head)
    if seen != "success":
        return False
    progress.reached(progress.ACCEPT, "paradym showed Go to wallet with no error heading")
    try:
        tapped = result_page.go_to_wallet(driver)
    except WebDriverException as exc:
        raise lost(ctx, "while tapping Go to wallet", exc) from exc
    if tapped:
        logger.info(f"[{ctx.flow}] Tapped Go to wallet")
    else:
        logger.warning(f"[{ctx.flow}] Go to wallet was gone before it could be tapped")
    return True


def _asked_again(ctx: FlowContext, name: str, timeout: float, after: str) -> FlowFailure:
    """A screen this flow had already answered was up again when the wait ran out.

    Its PIN screen is `setup`: paradym asking again for the PIN it was just given says the
    configured PIN was not accepted, which is ours to fix, not the provider's. Any other screen is
    `prompt_loop` — paradym re-asking instead of progressing — which is what that outcome names.
    """
    if name == "its PIN screen":
        return FlowFailure(outcome.SETUP, (
            f"[{ctx.flow}] paradym was showing its PIN screen again {timeout:.0f}s after {after} "
            f"for '{ctx.what}', after the PIN had been entered: the configured PIN was likely not "
            "accepted (PARADYM_APP_PIN)"
        ))
    return FlowFailure(outcome.PROMPT_LOOP, (
        f"[{ctx.flow}] paradym was showing {name} again {timeout:.0f}s after {after} for "
        f"'{ctx.what}', although it had already been answered, instead of progressing"
    ))


def nothing_appeared(driver, ctx: FlowContext, *, late, timeout: float, expected: str,
                     waited_for: str, after: str, head: str, was_in_front: bool = False,
                     fetching=None, answered=()) -> str:
    """A wait before Accept or Share ran out. Returns the name of a `late` screen that showed after
    all; otherwise raises the honest diagnosis.

    `late` is the wait's own (name, probe, probe timeout) list, re-probed once (`late_look`)
    together with `answered`, the screens this flow already answered: one of those still up is
    paradym asking again (`_asked_again`), never an unrecognised screen. Then
    paradym's error screen (`head` says where), a crash, its loading text when the caller passes
    a `fetching` probe, its home screen — one look each, at the end, so the message says what was
    there when checked rather than how long it had been there — and finally the shared diagnosis,
    whose "no error" and "no home screen" are then both true.

    `was_in_front` says paradym already showed a screen of its own in this flow (its PIN, a
    consent). Then a paradym no longer in front has *left*, and the shared diagnosis — "did not
    come to the foreground", with a check of the link's scheme — would describe a delivery that
    demonstrably worked.
    """
    during = f"after {after}"
    seen = late_look(driver, ctx, list(late) + list(answered), during=during)
    if seen in {name for name, _, _ in answered}:
        raise _asked_again(ctx, seen, timeout, after)
    if seen:
        progress.step(f"paradym showed {seen} late")
        return seen
    try:
        if error_page.present(driver, 1.0):
            progress.reached(progress.OPEN, "paradym was in front, showing its error screen")
            raise error_failure(driver, ctx, head)
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    raise_if_crashed(driver, ctx, when=during)
    try:
        still_fetching = bool(fetching) and fetching(driver, 1.0)
        on_home = not still_fetching and home_on_screen(driver, 1.0)
    except WebDriverException as exc:
        raise lost(ctx, during, exc) from exc
    if still_fetching:
        progress.reached(progress.OPEN, "paradym was in front, fetching")
        raise FlowFailure(outcome.PROCESSING, (
            f"[{ctx.flow}] paradym was still fetching when checked {timeout:.0f}s after {after} "
            f"for '{ctx.what}', and had not shown {waited_for}"
        ))
    if on_home:
        progress.reached(progress.OPEN, "paradym was in front, on its home screen")
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] paradym showed its home screen, not {waited_for}, when checked "
            f"{timeout:.0f}s after {after} for '{ctx.what}', and gave no reason"
        ))
    if was_in_front:
        try:
            front = driver.current_package
            state = _APP_STATES.get(driver.query_app_state(ctx.app_package), "unknown")
        except WebDriverException as exc:
            raise lost(ctx, during, exc) from exc
        if front != ctx.app_package:
            # The app state says whether paradym is still alive behind `front` or gone: a reader
            # can then tell a backgrounding from a silent exit. No crash is claimed — Android
            # showed no crash dialog, or raise_if_crashed above would have said so.
            raise FlowFailure(outcome.ABSENT, (
                f"[{ctx.flow}] paradym had left the foreground when checked {timeout:.0f}s after "
                f"{after} for '{ctx.what}' ({front or 'unknown'} was in front; Android reports "
                f"paradym as {state}): neither {waited_for}, its error screen nor its home screen "
                "was showing"
            ))
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected=expected, timeout=timeout)
