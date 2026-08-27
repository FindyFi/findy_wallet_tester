"""What happened after a deeplink was fired, as a fixed set of named outcomes.

Both hovi flows used to end in a single blocking wait on their success screen, so four different
failures (app never foregrounded, wallet still processing, wallet showed an error, wallet went home)
all surfaced as one phantom `Element ('xpath', '//*[@text="Accept"]') not found`, which reads like a
broken locator. Four of hovi's seven failures on 2026-08-19 were misreported that way.

These names become the categories the published report uses to say *why* a cell is red. "Your URL
never reached the wallet" and "the wallet rejected your credential" point at opposite parties.

`ABSENT` and `UNROUTABLE` look identical on screen, since in both cases nothing came forward, but
`UNROUTABLE` blames the provider's URL scheme and is only ever claimed with a device dump behind it
(see flows/deeplink.py).
"""
import logging
import time
from urllib.parse import urlsplit

from wallets.hovi.flows import deeplink
from wallets.hovi.pages import error_page, loading_page
from wallets.hovi.pages.home_page import on_screen as _home_on_screen

logger = logging.getLogger(__name__)

# Flow-decided: what the wallet did with a deeplink.
SUCCESS = "success"            # the expected screen appeared
REJECTED = "rejected"          # the wallet showed an error surface
PROCESSING = "processing"      # still on the spinner when time ran out
DISMISSED = "dismissed"        # returned home without offering anything
ABSENT = "absent"              # never came to the foreground
UNROUTABLE = "unroutable"      # ...because its scheme is not one the wallet registers
NO_MATCH = "no_match"          # showed the request, holds nothing that satisfies it

# Test-decided: what the wallet did with the credential.
NOT_STORED = "not_stored"                    # accepted, but the count did not move
NOTHING_TO_PRESENT = "nothing_to_present"    # asked to share from an empty wallet

# How long a glimpse of the home screen is ignored before it counts as DISMISSED. hovi can show
# home briefly while it works on the deeplink.
_HOME_GRACE = 5.0


class FlowFailure(RuntimeError):
    """A failure that knows which outcome it was.

    The category is an attribute rather than something to parse back out of the message, so the
    report can group by it without matching on prose.
    """

    def __init__(self, category: str, message: str):
        super().__init__(f"[{category}] {message}")
        self.category = category


def wait_for(driver, on_success, timeout: float, error_before: bool) -> str:
    """Poll until the success screen, a fresh error, or home settles. Returns an outcome name.

    `on_success` is the page's own `on_screen(driver, timeout=…)`, so each flow supplies the one
    screen that means it worked and shares everything else.

    `error_before` says whether the error banner was already showing when the deeplink was fired.
    It survives until the app restarts (see pages/error_page.py), so one that was already there
    says nothing about this case.
    """
    start = time.time()
    end = start + timeout
    saw_loading = False

    while time.time() < end:
        if on_success(driver, timeout=1):
            return SUCCESS

        if not error_before and error_page.present(driver, timeout=0.5):
            return REJECTED

        if loading_page.on_screen(driver, timeout=0.5):
            saw_loading = True
            continue

        # Home only counts once the grace period has passed, so a momentary home screen during
        # processing is not mistaken for a finished flow.
        if time.time() - start > _HOME_GRACE and _home_on_screen(driver, timeout=0.5):
            return DISMISSED

    return PROCESSING if saw_loading else ABSENT


def _foreground(driver) -> str:
    try:
        return driver.current_package or "unknown"
    except Exception:
        return "unknown"


def raise_for(state: str, driver, flow: str, expected: str, what: str, timeout: float,
              error_before: bool, app_package: str = "", url: str = ""):
    """Turn a non-success outcome into a diagnosis rather than a missing-locator complaint.

    `expected` names what should have appeared ("credential offer", "information request"), so one
    function serves both flows.
    """
    if state == REJECTED:
        raise FlowFailure(REJECTED, (
            f"[{flow}] hovi rejected '{what}': \"Please check if the QR is correct and try "
            f"again\". It could not process the {expected}"
        ))

    if state == PROCESSING:
        raise FlowFailure(PROCESSING, (
            f"[{flow}] hovi was still on its processing screen after {timeout:.0f}s for "
            f"'{what}'. The {expected} never rendered"
        ))

    if state == DISMISSED:
        raise FlowFailure(DISMISSED, (
            f"[{flow}] hovi returned to its home screen without showing a {expected} for "
            f"'{what}'. The deeplink was accepted by the app but produced nothing"
        ))

    # ABSENT: nothing recognisable ever appeared. Before blaming the wallet, ask the device
    # whether this URL could have reached it at all.
    reason = ""
    if app_package and url:
        try:
            reason = deeplink.unroutable_reason(driver, app_package, url)
        except Exception as exc:  # a diagnosis must never replace the failure it explains
            logger.warning(f"[{flow}] Could not read registered schemes: {exc}")

    if reason:
        # Deterministic: the same URL cannot become routable on a retry, and the suite's default
        # is two reruns. The tag is what `--rerun-except=no_retry` in pytest.ini keys off.
        raise FlowFailure(UNROUTABLE, f"[{flow}] Could not deliver '{what}': {reason} [no_retry]")

    # Not unroutable, so name the scheme that *was* used: otherwise this failure reads the same
    # whether the URL was deliverable or not.
    scheme = urlsplit(url).scheme.lower() if url else ""
    used = (f" The URL used the {scheme}:// scheme, which hovi does register, so it had the "
            "chance to take it." if scheme else "")
    stale = (" A stale error banner from an earlier test was already on screen, so hovi may have "
             "rejected this one too." if error_before else "")
    raise FlowFailure(ABSENT, (
        f"[{flow}] Nothing appeared for '{what}' after {timeout:.0f}s. No {expected}, no error "
        f"and no home screen. hovi did not come to the foreground "
        f"({_foreground(driver)} was).{used}{stale}"
    ))


def raise_if_rejected(driver, flow: str, what: str, action: str, error_before: bool):
    """Fail with `rejected` if hovi's error banner appeared after an action succeeded.

    Reaching the next screen is not the same as the wallet being happy. authbound_issuer and
    sphereon_issuer both showed the offer, took the Accept and then raised the banner, which went
    unnoticed and was reported as `not_stored`.

    Skipped when a banner was already showing, since then its presence proves nothing.
    """
    if error_before or not error_page.present(driver, timeout=2):
        return
    raise FlowFailure(REJECTED, (
        f"[{flow}] hovi {action} '{what}' and then failed: \"Please check if the QR is correct "
        f"and try again\". The step completed but the wallet rejected the result"
    ))
