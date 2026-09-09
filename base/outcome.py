"""What happened after a deeplink was fired, as a fixed set of named outcomes.

Wallet flows used to end in a single blocking wait on their success screen, so four different
failures (app never foregrounded, wallet still processing, wallet showed an error, wallet went home)
all surfaced as one phantom `Element ('xpath', '//*[@text="Accept"]') not found`, which reads like a
broken locator. Four of hovi's seven failures on 2026-08-19 were misreported that way.

These names become the categories the published report uses to say *why* a cell is red. "Your URL
never reached the wallet" and "the wallet rejected your credential" point at opposite parties.

Generalised from `wallets/hovi/flows/outcome.py`, which proved the vocabulary on one wallet. What it
gained on the way out, each from a hand-written loop somewhere else that already knew it:

- **interstitials** — hovi's loop polled for outcome screens and nothing else, so a wallet waiting
  on a biometric prompt was reported `absent`. That single gap is why authbound, heidi, gataca and
  procivis could not adopt this.
- **deadline extension** — from authbound's `wait_for_outcome`: a visible spinner is the wallet
  telling us it is still working, so it buys time instead of running the clock out.
- **lost session** — from authbound and heidi: a dead UiAutomator2 session is not a wallet verdict.
  hovi reported it as `absent`, which blames the wallet for the harness falling over.
- **crash / ANR** — from paradym's and toppan's `check_for_error`.

`ABSENT` and `UNROUTABLE` look identical on screen, since in both cases nothing came forward, but
`UNROUTABLE` blames the provider's URL scheme and is only ever claimed with a device dump behind it
(see base/deeplink.py).
"""
import logging
import time
from urllib.parse import urlsplit

from selenium.common.exceptions import WebDriverException

from base import deeplink, interstitials as _interstitials
from base.android import SystemOverlay, detect_crash_or_anr

logger = logging.getLogger(__name__)

# Flow-decided: what the wallet did with a deeplink.
SUCCESS = "success"            # the expected screen appeared
REJECTED = "rejected"          # the wallet showed an error surface
PROCESSING = "processing"      # still on the spinner when time ran out
DISMISSED = "dismissed"        # returned home without offering anything
ABSENT = "absent"              # never came to the foreground
UNROUTABLE = "unroutable"      # ...because its scheme is not one the wallet registers
NO_MATCH = "no_match"          # showed the request, holds nothing that satisfies it
PROMPT_LOOP = "prompt_loop"    # kept re-asking for the same thing instead of progressing
CRASHED = "crashed"            # the app died or stopped responding

# Harness-decided: not a statement about the wallet at all. Kept separate so a report can exclude
# it rather than publishing our own breakage as a vendor's failure.
LOST_SESSION = "lost_session"

# Test-decided: what the wallet did with the credential.
NOT_STORED = "not_stored"                    # accepted, but the count did not move
NOTHING_TO_PRESENT = "nothing_to_present"    # asked to share from an empty wallet

# One poll pass. Short, because the loop is the wait: a probe that blocks for a second makes every
# other probe in the same tick that much later, and the ordering below is what carries the meaning.
_TICK = 0.4

# Crash scans are the most expensive probe per tick and the rarest to hit, so they are throttled
# rather than run every pass. One unconditional scan happens before any non-success verdict.
_CRASH_SCAN_INTERVAL = 5.0


class FlowFailure(RuntimeError):
    """A failure that knows which outcome it was.

    The category is an attribute rather than something to parse back out of the message, so the
    report can group by it without matching on prose.

    The `[category]` prefix in the text is not redundant with that: `pytest.ini` runs
    `--rerun-except=no_retry`, and pytest-rerunfailures matches a regex against
    ``f"{type.__name__}: {value}"``. The message is the only channel that reaches it.
    """

    def __init__(self, category: str, message: str):
        super().__init__(f"[{category}] {message}")
        self.category = category


def _a(noun: str) -> str:
    """"a credential offer" but "an information request" — these strings get published."""
    return "an" if noun[:1].lower() in "aeiou" else "a"


def _foreground(driver) -> str:
    try:
        return driver.current_package or "unknown"
    except Exception:
        return "unknown"


def _error_text(driver, screens) -> str:
    """The wallet's own error copy, or "" if it cannot be read. Never raises."""
    reader = screens.error_text
    if not reader:
        return ""
    try:
        return (reader(driver) or "").strip()
    except Exception as exc:
        logger.warning(f"[outcome] Could not read {screens.name}'s error text: {exc}")
        return ""


def wait_for(driver, screens, ctx, *, target, timeout: float, error_before: bool = False,
             interstitials=()) -> str:
    """Poll until the target screen, a fresh error, or home settles. Returns an outcome name.

    `target` is the page's own `on_screen(driver, timeout)` — the one screen that means this
    particular step worked. Everything else is shared.

    `error_before` says whether the wallet's error surface was already showing when the deeplink was
    fired; one that was already there says nothing about this case. Only wallets whose error surface
    persists need it (see `Screens.error_sticky`).

    Never raises for a wallet-side outcome; `raise_for` turns the name into a diagnosis. It does
    return `LOST_SESSION` if the driver dies, because after that no further probe is meaningful.
    """
    start = time.time()
    end = start + timeout
    extended = 0.0
    saw_processing = False
    fired = ctx.fired
    fired.clear()
    last_crash_scan = start

    error_probe = screens.probe("error")
    processing_probe = screens.probe("processing")

    while time.time() < end:
        try:
            # Interstitials first: whatever is covering the screen has to go before anything else
            # on it can be read. Answering one is progress, so it may buy the wait more time.
            acted = _interstitials.service(driver, ctx, interstitials, fired)
            if acted is not None:
                if acted.extends and extended < screens.processing_grace:
                    grant = min(acted.extends, screens.processing_grace - extended)
                    end += grant
                    extended += grant
                continue

            if target(driver, _TICK):
                return SUCCESS

            if error_probe and not error_before and error_probe(driver, _TICK):
                return REJECTED

            if processing_probe and processing_probe(driver, _TICK):
                # A visible spinner is the wallet saying it is still working. Waiting is progress,
                # up to the wallet's own budget — authbound's issuance genuinely takes minutes.
                saw_processing = True
                if extended < screens.processing_grace:
                    grant = min(_TICK, screens.processing_grace - extended)
                    end += grant
                    extended += grant
                continue

            # Home only counts once the grace period has passed, so a momentary home screen during
            # processing is not mistaken for a finished flow.
            if time.time() - start > screens.home_grace and screens.home(driver, _TICK):
                return DISMISSED

            if time.time() - last_crash_scan > _CRASH_SCAN_INTERVAL:
                last_crash_scan = time.time()
                if detect_crash_or_anr(driver) is not None:
                    return CRASHED

        except WebDriverException as exc:
            # The session died mid-wait. That is our failure, not the wallet's, and reporting it as
            # `absent` would put a red cell against a vendor for a UiAutomator2 crash.
            logger.warning(f"[{ctx.flow}] {screens.name}: Appium session lost mid-wait: {exc}")
            return LOST_SESSION

    # One unconditional scan before writing a negative verdict: a crash is the explanation for every
    # other name we might otherwise pick.
    try:
        if detect_crash_or_anr(driver) is not None:
            return CRASHED
    except WebDriverException:
        return LOST_SESSION

    if _interstitials.over_budget(interstitials, fired):
        return PROMPT_LOOP
    return PROCESSING if saw_processing else ABSENT


def raise_for(state: str, driver, screens, ctx, *, expected: str, timeout: float,
              error_before: bool = False, interstitials=()):
    """Turn a non-success outcome into a diagnosis rather than a missing-locator complaint.

    `expected` names what should have appeared ("credential offer", "information request"), so one
    function serves every flow.
    """
    if state == SUCCESS:
        raise ValueError("raise_for called with SUCCESS — the caller should have returned")

    wallet, flow, what = screens.name, ctx.flow, ctx.what

    if state == LOST_SESSION:
        raise FlowFailure(LOST_SESSION, (
            f"[{flow}] The Appium session died while waiting for {_a(expected)} {expected} for '{what}'. "
            f"This is a harness failure, not a verdict about {wallet} or the provider"
        ))

    if state == CRASHED:
        overlay = None
        try:
            overlay = detect_crash_or_anr(driver)
        except Exception:
            pass
        what_happened = ("stopped responding" if overlay is SystemOverlay.ANR else "crashed")
        raise FlowFailure(CRASHED, (
            f"[{flow}] {wallet} {what_happened} while handling '{what}'. Android showed its "
            f"{'not-responding' if overlay is SystemOverlay.ANR else 'crash'} dialog, so no "
            f"{expected} could appear"
        ))

    if state == REJECTED:
        said = _error_text(driver, screens)
        quoted = f": \"{said}\"" if said else ""
        raise FlowFailure(REJECTED, (
            f"[{flow}] {wallet} rejected '{what}'{quoted}. It could not process the {expected}"
        ))

    if state == PROMPT_LOOP:
        names = ", ".join(_interstitials.over_budget(interstitials, ctx.fired)) or "a prompt"
        raise FlowFailure(PROMPT_LOOP, (
            f"[{flow}] {wallet} kept re-showing {names} for '{what}' instead of progressing to "
            f"{_a(expected)} {expected}. Every prompt was answered and a fresh one appeared immediately"
        ))

    if state == PROCESSING:
        raise FlowFailure(PROCESSING, (
            f"[{flow}] {wallet} was still on its processing screen after {timeout:.0f}s for "
            f"'{what}'. The {expected} never rendered"
        ))

    if state == DISMISSED:
        raise FlowFailure(DISMISSED, (
            f"[{flow}] {wallet} returned to its home screen without showing {_a(expected)} {expected} for "
            f"'{what}'. The deeplink was accepted by the app but produced nothing"
        ))

    # ABSENT: nothing recognisable ever appeared. Before blaming the wallet, ask the device
    # whether this URL could have reached it at all.
    reason = ""
    if ctx.app_package and ctx.url:
        try:
            reason = deeplink.unroutable_reason(driver, ctx.app_package, ctx.url, wallet)
        except Exception as exc:  # a diagnosis must never replace the failure it explains
            logger.warning(f"[{flow}] Could not read registered schemes: {exc}")

    if reason:
        # Deterministic: the same URL cannot become routable on a retry, and the suite's default
        # is two reruns. The tag is what `--rerun-except=no_retry` in pytest.ini keys off.
        raise FlowFailure(UNROUTABLE, f"[{flow}] Could not deliver '{what}': {reason} [no_retry]")

    # Not unroutable, so name the scheme that *was* used: otherwise this failure reads the same
    # whether the URL was deliverable or not.
    scheme = urlsplit(ctx.url).scheme.lower() if ctx.url else ""
    used = (f" The URL used the {scheme}:// scheme, which {wallet} does register, so it had the "
            "chance to take it." if scheme else "")
    stale = (" A stale error banner from an earlier test was already on screen, so the wallet may "
             "have rejected this one too." if error_before else "")
    # Say what was never looked for, rather than implying every surface was checked and clean.
    blind = screens.unmapped()
    unmapped = (f" No {'/'.join(blind)} screen is mapped for {wallet}, so one may have been showing "
                "unrecognised." if blind else "")
    enrolled = (" Android offered to enrol a fingerprint during the wait, so the wallet may be "
                "asking for a key this device cannot produce."
                if _interstitials.ENROLLMENT_OFFERED in ctx.notes else "")
    # Only claim to have seen nothing on the surfaces actually probed. Saying "no error" about a
    # wallet whose error screen is unmapped states as fact something nobody checked.
    looked = [f"no {expected}"]
    if screens.probe("error"):
        looked.append("no error")
    looked.append("no home screen")
    nothing = f"{', '.join(looked[:-1])} and {looked[-1]}"
    raise FlowFailure(ABSENT, (
        f"[{flow}] Nothing appeared for '{what}' after {timeout:.0f}s: {nothing}. "
        f"{wallet} did not come to the foreground "
        f"({_foreground(driver)} was).{used}{stale}{unmapped}{enrolled}"
    ))


def raise_if_rejected(driver, screens, ctx, *, action: str, error_before: bool = False):
    """Fail with `rejected` if the wallet's error surface appeared after an action succeeded.

    Reaching the next screen is not the same as the wallet being happy. authbound_issuer and
    sphereon_issuer both showed hovi the offer, took the Accept and then raised its banner, which
    went unnoticed and was reported as `not_stored`.

    Skipped when the surface was already showing, since then its presence proves nothing, and a
    no-op for a wallet with no mapped error screen.
    """
    error_probe = screens.probe("error")
    if error_before or not error_probe or not error_probe(driver, 2):
        return
    said = _error_text(driver, screens)
    quoted = f": \"{said}\"" if said else ""
    raise FlowFailure(REJECTED, (
        f"[{ctx.flow}] {screens.name} {action} '{ctx.what}' and then failed{quoted}. The step "
        "completed but the wallet rejected the result"
    ))


def raise_no_match(screens, ctx, *, said: str):
    """The wallet showed the request and answered that it holds nothing satisfying it.

    Kept apart from `nothing_to_present`: this is a credential-type disagreement with a non-empty
    wallet, which is a finding about the verifier's request, not about the wallet being empty.
    """
    raise FlowFailure(NO_MATCH, (
        f"[{ctx.flow}] {screens.name} received the request for '{ctx.what}' and answered "
        f"\"{said}\". It holds nothing that satisfies what this verifier asked for"
    ))
