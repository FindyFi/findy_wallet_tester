import json
import logging
import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from base import outcome, progress
from base.base_page import BasePage
from base.outcome import FlowFailure
from base.utils import wait_present
from wallets.procivis.pages.invitation_error_details_page import (
    InvitationErrorDetailsPage,
    SCREEN_ID as _DETAILS_SCREEN_ID,
)

logger = logging.getLogger(__name__)

# InvitationProcessScreen — procivis's screen for handling an invitation, before any offer or
#   request is shown. Where it fails when the invitation itself cannot be fetched: authbound and
#   paradym issuance ("Connection failed"), and paradym and waltid verification.
# CredentialAcceptProcessScreen — the screen for accepting an offer ("Credential offering").
#   waltid's issuance fails here ("Issuance failed") without the framework ever seeing the offer.
INVITATION_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="InvitationProcessScreen"]')
ACCEPT_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="CredentialAcceptProcessScreen"]')
SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="InvitationProcessScreen" or @resource-id="CredentialAcceptProcessScreen"]')

# Generic locators that match either process screen prefix.
#
# procivis draws a failure with one of two Lottie animations and they are NOT interchangeable:
# `.animation.error` is the hard rejection, `.animation.warning` the transport/connection failure
# ("Connection failed"). Measured across every XML dump this repo has captured: 42 `.warning`
# against 16 `.error`, and CredentialAcceptProcessScreen has only ever shown `.warning` — the
# `@text="Issuance failed"` alternative was the sole reason that screen was ever caught at all.
#
# Matching only `.error` meant the common failure fell through to the timeout at the end of the
# wait below, and the details screen behind `.header.info` — which is on screen, in those very
# dumps — was never opened. Four of five issuance failures in the 2026-09-21 run reported
# "did not complete within timeout" and no Code/Message/Cause because of this.
#
# Both variants are named explicitly rather than matching `contains(@resource-id, ".animation.")`:
# the wait below treats any `_error` hit as terminal, so a success animation caught mid-dismissal
# would turn a passing issuance red.
_error = (
    AppiumBy.XPATH,
    '//*[contains(@resource-id, ".animation.error")'
    ' or contains(@resource-id, ".animation.warning")'
    ' or @text="Issuance failed"]',
)
_info = (AppiumBy.XPATH, '//*[contains(@resource-id, ".header.info")]')
_show_more = (AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen.cause.expandValueButton"]')

# The label that carries the wallet's own words. Read off 75 captured dumps, procivis has exactly
# three: "Connection failed" (.animation.warning, 42), "No credential interaction found."
# (.animation.error, 16) and "Issuance failed" (CredentialAcceptProcessScreen, 17). The text sits on
# the label node itself, not a child.
_label = (AppiumBy.XPATH, '//*[contains(@resource-id, "ProcessScreen.label") and @text!=""]')


def present(driver, timeout: float = 2) -> bool:
    """Is procivis showing a failed outcome? The `error` probe for base/screens.py.

    The process screen alone is not an error — it is also what the wallet shows while it works. The
    failure animation is the signal, which is why this reuses `_error` rather than SCREEN_ID.
    """
    return wait_present(driver, _error, timeout=timeout)


def message(driver) -> str:
    """procivis's own error copy, read without touching the screen. "" when it is not showing one.

    Read-only on purpose. The Code/Message/Cause behind the "?" is richer, but collecting it means
    tapping through a screen a test has just failed on — that belongs in `_failures.rejected`, at
    the moment of failure, and not in a teardown-time reader. `record_error_screen` calls this after the
    fact and explicitly does no UI interaction; see its docstring on why paradym's scrape was both
    fragile and destructive.

    Gated on `present` so a processing screen's label is never published as an error.
    """
    if not present(driver, timeout=1):
        return ""
    found = driver.find_elements(*_label)
    return (found[0].get_attribute("text") or "").strip() if found else ""


def quoted(text: str) -> str:
    """': "<label>"' for procivis's own words; an unquoted note when there were none to read.

    The app.log digest publishes the first double-quoted span of a failure as what the wallet
    said, so a placeholder must never be put in quotes.
    """
    return f': "{text}"' if text else " (procivis's failure label could not be read)"


_warning = (AppiumBy.XPATH, '//*[contains(@resource-id, ".animation.warning")]')


def warning_on_screen(driver):
    """True for the `.animation.warning` failure, False for `.animation.error`, None when it cannot
    be read. Read-only, one call; for the `[no_retry]` rule in flows/_failures.py."""
    try:
        return bool(driver.find_elements(*_warning))
    except Exception:
        return None


def screen_name(driver) -> str:
    """Which of the two process screens is up, for the trail. Read-only; never raises.

    "process screen" when it cannot be read: naming a screen nobody saw would be a claim.
    """
    try:
        if driver.find_elements(*ACCEPT_SCREEN_ID):
            return "CredentialAcceptProcessScreen"
        return "InvitationProcessScreen"
    except Exception:
        return "process screen"


def read_details(driver, **page_kwargs) -> dict:
    """procivis's Code/Message/Cause behind the "?" on a failed process screen. Never raises.

    The one place both flows — issuance and verification — open the details screen. Taps, in
    order: the "?" (`.header.info`), then, only once InvitationErrorDetailsScreen is seen in front,
    the cause's expand button if there is one. Nothing else: the details screen is deliberately
    LEFT OPEN, cause expanded, so teardown's XML dump and screenshot record procivis's Code,
    Message and full Cause rather than the one-line label. Teardown's init_flow closes it
    afterwards (`_close_error_details`). A long cause may run below the fold; nothing scrolls it.

    When the details screen never opened — "?" missing, or a tap procivis ignored — nothing else
    is touched and the failure screen stays in front for the same evidence.

    Call it after reading the label: the label belongs to the screen this taps away from.
    """
    page = BasePage(driver, **page_kwargs)
    details: dict = {}
    try:
        if not wait_present(driver, _info, timeout=1):
            logger.info("[procivis] Failure screen has no details button — label only")
            return details
        page.click(_info)
        if not wait_present(driver, _DETAILS_SCREEN_ID, timeout=2):
            # The tap was dispatched but the details screen never came up: still on the failure
            # screen, which is left as it is for teardown's evidence.
            logger.warning("[procivis] Tapped '?' but the error details screen never opened")
            return details
    except Exception as exc:
        logger.warning(f"[procivis] Could not open the error details screen: {exc}")
        return details
    try:
        # Expanding the cause is best-effort and guarded *separately*: not every error carries an
        # expandable cause, and one absent button must not cost us the Code and Message that are
        # already on screen. Folded into one try, a missing expand button discarded the entire
        # details screen and the block fell back to "no details available".
        # A one-second look first: a missing expander must not cost the page's full click
        # timeout (10 s on device) on a failure path.
        if wait_present(driver, _show_more, timeout=1):
            try:
                page.click(_show_more, timeout=2)
            except Exception:
                logger.debug("[procivis] Could not expand the cause")
        else:
            logger.debug("[procivis] Details screen has no expandable cause")
        details = InvitationErrorDetailsPage(driver, **page_kwargs).collect_details()
    except Exception as exc:
        # Never fatal — this runs on a screen a test has already failed on, and the failure being
        # described matters more than the description. But say so: a silent `pass` here is
        # indistinguishable from a wallet that genuinely reported nothing.
        logger.warning(f"[procivis] Could not read the error details screen: {exc}")
    # Left open on purpose: the details screen is the failure evidence (see the docstring).
    return details


def _one_line(text) -> str:
    """Every run of whitespace, newlines included, as one space."""
    return " ".join(str(text).split())


_RESPONSE_BODY = re.compile(r"response_body:\s*`?(\{.*\})`?\s*$")


def _compact(line: str) -> str:
    """One cause line, with an HTTP response body reduced to its own message when it has one.

    walt.id answers with a JSON body whose only useful part is `message` ("Field 'format' is
    required …"); quoted whole it fills the published tooltip before reaching it.
    """
    m = _RESPONSE_BODY.search(line)
    if m:
        try:
            body = json.loads(m.group(1))
            said = next((body[k] for k in ("message", "error_description", "error", "detail")
                         if isinstance(body, dict) and isinstance(body.get(k), str)
                         and body[k].strip()), "")
        except ValueError:
            said = ""
        if said:
            before = line[:m.start()].rstrip(" ,:")
            line = f"{before} — {said}" if before else said
    # Not normalised here: `details_line` makes the whole result one line, in one place, so no
    # piece — a JSON `message` with a newline in it included — can escape it.
    return line


def details_line(details: dict) -> str:
    """procivis's Code/Message/Cause as ONE line, the deepest cause first. "" when there are none.

    Only a failure's first line reaches summary.json and the app.log digest, and the report cuts its
    tooltip at 200 characters — so the multi-line "Caused by:" chain is joined, innermost first,
    because the innermost cause ("HTTP error: error sending request for url (https://…)") is the
    one that names the party at fault. Double quotes become single: the digest publishes a
    failure's first double-quoted span as what the wallet said, and that is the label's place.
    """
    rest = {_one_line(k): str(v) for k, v in (details or {}).items()
            if v is not None and str(v).strip()}
    code = _one_line(rest.pop("Code", ""))
    msg = _one_line(rest.pop("Message", ""))
    chain = []
    # Split on the "Caused by:" markers rather than on newlines, so a pretty-printed response body
    # stays one link of the chain instead of becoming a dozen.
    for piece in re.split(r"(?:^|\n)\s*caused by:", rest.pop("Cause", ""), flags=re.IGNORECASE):
        text = _one_line(piece)
        if text:
            chain.append(_compact(text))
    if chain:
        main = " ← ".join(reversed(chain))
        meta = f"{code}: {msg}" if code and msg else code or msg
    else:
        main, meta = msg, code
    line = (f"{main} ({meta})" if main and meta else main or meta)
    extra = "; ".join(f"{k}: {_one_line(v)}" for k, v in rest.items())
    line = f"{line}; {extra}" if line and extra else line or extra
    # One line, whatever any piece carried: only a failure's first line is published, and the
    # Code/Message and [no_retry] come after the cause.
    return _one_line(line).replace('"', "'")


# What `CredentialAcceptProcessPage.wait_for_result` saw. The flow turns FAILED into `rejected`
# through flows/_failures.py, so the label, the details and [no_retry] are decided in one place.
FAILED = "failed"
CLEARED = "cleared"


def _settled(driver):
    """FAILED if the failure animation is up, CLEARED once the process screen is gone, else False.

    Failure first: the animation sits on the process screen, so a screen still showing one is never
    "cleared". Like `EC.any_of`, a driver error on one poll counts as "not yet" — the timeout branch
    below re-probes, and a dead session raises there.
    """
    for result, condition in ((FAILED, EC.presence_of_element_located(_error)),
                              (CLEARED, EC.invisibility_of_element_located(SCREEN_ID))):
        try:
            if condition(driver):
                return result
        except WebDriverException:
            pass
    return False


class CredentialAcceptProcessPage(BasePage):
    def wait_for_result(self) -> str:
        """Wait on procivis's process screen. Returns FAILED or CLEARED; raises `processing`.

        CLEARED: the screen went away with no failure animation — what success looks like, and
                 also what a screen that had already gone looks like. Not a verdict: the flow
                 looks for a crash dialog, and the test's count decides.
        FAILED:  the failure animation is up. Nothing is read or tapped here.
        Timeout: looked at once more — a failure animation is still FAILED, the screen still up
                 without one is `processing`, and a screen gone by then is CLEARED.

        A WebDriverException from a probe propagates: the flow makes it `lost_session`.
        """
        t = self._get_timeout("credential_offer")
        if not wait_present(self.driver, SCREEN_ID, timeout=1):
            logger.info("[credential_accept] Process screen had already closed")
            progress.step("procivis's process screen had already closed")
            return CLEARED

        try:
            return WebDriverWait(self.driver, t).until(_settled)
        except TimeoutException:
            pass

        # A UiAutomator2 blip swallowed by `_settled`, or an animation that landed in the last
        # poll's sleep, also ends here. Failure first, for the same reason as in `_settled`.
        if present(self.driver, timeout=1):
            return FAILED
        if not wait_present(self.driver, SCREEN_ID, timeout=1):
            # Gone by now, with the session answering: most likely it cleared during the last
            # sleep, which is what success looks like. The trail says it was late.
            logger.info("[credential_accept] Process screen cleared after the wait ran out")
            progress.step("procivis's process screen cleared only after the wait ran out")
            return CLEARED
        # Still up, with no failure animation: procivis was still working. Evidence of
        # processing, not of an error, so it is not reported as one.
        raise FlowFailure(outcome.PROCESSING, (
            f"[credential_flow] procivis was still on its process screen after {t:.0f}s, with no "
            "failure animation: credential issuance did not complete within the timeout"
        ))
