"""What both procivis flows do when something goes wrong, written once.

Per-wallet on purpose: every probe and every sentence here is procivis's own — base/screens.py
says why a copied probe is worse than none. What does not name procivis's screens lives in
base/flow_failures.py (`lost`, re-exported here, so flows keep calling `failures.lost`).

- `lost`: a dead UiAutomator2 session is ours, not procivis's (re-exported from base).
- `unlock`: procivis's biometric prompt and PIN screen after a deeplink.
- `rejected`: procivis's own failure screen — its label, then its Code/Message/Cause — as one
  `rejected` FlowFailure, with the `[no_retry]` rule in one place.
- `raise_if_crashed`: base's crash look, told to log a dead session rather than raise it.
- `require_something_to_present`: the verification test's empty-wallet precondition.
"""
import logging

from selenium.common.exceptions import WebDriverException

from base import credential_count, flow_failures, outcome, progress
from base.flow_context import FlowContext
from base.flow_failures import lost  # re-exported; lives in base/flow_failures.py
from base.outcome import FlowFailure
from wallets.procivis.pages import pin_page as pin_screen
from wallets.procivis.pages.credential_accept_process_page import (
    details_line,
    message as failure_label,
    quoted,
    read_details,
    warning_on_screen,
)
from wallets.procivis.pages.home_page import HomePage, holds_nothing
from wallets.procivis.pages.pin_page import PinPage, biometric_on_screen

logger = logging.getLogger(__name__)

# reqwest's transport error — the request never got an HTTP answer. Seen as BR_0347 "HTTP error:
# error sending request for url (…)", which later passed against the same issuer: not a verdict
# about the counterparty, so a rerun is worth having.
_TRANSPORT_FAILURE = "error sending request"


def unlock(driver, ctx: FlowContext) -> None:
    """Answer procivis's biometric prompt (USE PIN CODE) and its PIN screen after a deeplink.

    A missing PIN is our configuration, not the wallet's behaviour: `setup`.
    """
    try:
        if biometric_on_screen(driver):
            if not ctx.pin:
                raise FlowFailure(outcome.SETUP, (
                    f"[{ctx.flow}] App showed biometric prompt after deeplink — pass pin= to "
                    f"{ctx.flow}.run()"
                ))
            logger.info(f"[{ctx.flow}] Biometric prompt — falling back to PIN")
            PinPage(driver, **ctx.page_args).dismiss_biometric_prompt()
            progress.step("answered the biometric prompt with USE PIN CODE")

        if pin_screen.on_screen(driver):
            # PinCodeCheckScreen is procivis's own screen, so it was in front after the delivery.
            progress.reached(progress.OPEN, "procivis showed its PIN screen")
            if not ctx.pin:
                raise FlowFailure(outcome.SETUP, (
                    f"[{ctx.flow}] App showed PIN screen after deeplink — pass pin= to "
                    f"{ctx.flow}.run()"
                ))
            logger.info(f"[{ctx.flow}] PIN screen appeared — unlocking")
            PinPage(driver, **ctx.page_args).enter_login_pin(ctx.pin)
            progress.step("entered the wallet PIN")
    except WebDriverException as exc:
        raise lost(ctx, "while answering the unlock prompts", exc) from exc


def keeps_no_retry(details: str, warning) -> bool:
    """Whether a rejection is deterministic enough that a rerun would only repeat it.

    Yes, except: a transport failure in the cause chain (the request never got an answer), and a
    `.animation.warning` failure — or one whose animation could not be read — with no readable
    details, because then nothing says what failed. "Connection failed" with a readable cause such
    as BR_0081 "Wallet instance must be active" is still a verdict.
    """
    if _TRANSPORT_FAILURE in details.lower():
        return False
    if not details and warning is not False:
        return False
    return True


def rejected(driver, ctx: FlowContext, head: str):
    """Raise procivis's own failure screen as `rejected`. Never returns.

    Reads, in order: the label (it belongs to the screen the "?" taps away from), which failure
    animation is up, then the Code/Message/Cause behind the "?" (`read_details`, which leaves the
    details screen open as the failure evidence). None of the three can raise: an unreadable label
    is said unquoted, unreadable details are said in one fixed sentence, and the verdict stands.
    """
    try:
        said = failure_label(driver)
    except Exception as exc:
        logger.warning(f"[{ctx.flow}] Could not read the failure label: {exc}")
        said = ""
    warning = warning_on_screen(driver)
    raw = read_details(driver, **ctx.page_args)
    details = details_line(raw)
    logger.error(f"[{ctx.flow}] procivis failed '{ctx.what}' — {raw or 'no details'}")
    if details:
        tail = f" — {details}"
    else:
        # The label's own full stop is procivis's; ours would double it.
        stop = "" if said and said[-1] in ".!?" else "."
        tail = f"{stop} procivis's error details could not be read"
    retry = " [no_retry]" if keeps_no_retry(details, warning) else ""
    raise FlowFailure(outcome.REJECTED, f"{head}{quoted(said)}{tail}{retry}")


def raise_if_crashed(driver, ctx: FlowContext, when: str) -> None:
    """One look for Android's crash or not-responding dialog, where a screen closing would
    otherwise be taken for success. Never raises anything but `crashed`: a failed look claims
    nothing, a dead session included (it is logged, as it always was in procivis's flows)."""
    flow_failures.raise_if_crashed(driver, ctx, when, dead_session_is_lost=False)


def require_something_to_present(driver, page_args: dict, *, issuer: str, case: str) -> None:
    """Fail as `nothing_to_present` only when procivis itself says its wallet is empty.

    An empty wallet cannot present anything, and that points at an earlier issuance rather than at
    the verifier. procivis has no mapped no-match screen, so without this the two would be one. A
    count of 0 alone is not enough — a list still rendering reads 0 too — so it takes procivis's
    own empty state as well (`holds_nothing`). Read-only, on the wallet screen the test starts on.
    """
    held = credential_count.read(HomePage(driver, **page_args), when="before verification")
    if held is None:
        logger.info("[test] procivis's credential count before verification could not be read")
    else:
        logger.info(f"[test] procivis holds {held} credentials before verification")
    if holds_nothing(driver, held):
        # Unquoted: these are our words about procivis's screen, and the app.log digest publishes
        # a failure's first double-quoted span as what the wallet said.
        outcome.fail(outcome.NOTHING_TO_PRESENT, (
            f"Cannot verify '{case}' against '{issuer}': procivis holds no credentials (its wallet "
            "screen shows its empty state), so nothing could be shared. If issuance ran earlier in "
            "this run, it left nothing behind."
        ))
    if held == 0:
        logger.info("[test] Count read 0 but procivis showed no empty state — trying the verifier")
