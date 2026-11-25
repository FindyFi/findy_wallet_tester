import logging
import time as _time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.android import handle_biometric_if_present
from base.flow_context import FlowContext
from base.flow_failures import lost_session
from base.outcome import FlowFailure
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.heidi.pages.home_page import SCREEN_ID as _home_id, on_screen as _home_on_screen
from wallets.heidi.pages.connection_page import (
    ConnectionPage,
    SCREEN_ID as _connection_id,
    REFUSED_ID as _connection_refused_id,
)
from wallets.heidi.pages.error_page import (
    ErrorPage,
    SCREEN_ID as _error_id,
    VERIFICATION_SCREEN_ID as _verification_error_id,
    quoted,
)
from wallets.heidi.pages.verification_success_page import (
    VerificationSuccessPage,
    SCREEN_ID as _shared_id,
)
from wallets.heidi.pages.verification_request_page import (
    VerificationRequestPage,
    SCREEN_ID as _request_id,
    no_match as _no_match_on_screen,
)
from wallets.heidi.screens import SCREENS

logger = logging.getLogger(__name__)


def _wait_for_request(driver, request_id, error_id, verification_error_id,
                      connection_id, connection_refused_id, timeout: float):
    """Poll until the Information Request screen or an error screen appears.

    Two interstitial screens can appear mid-wait, in either order and more than
    once, so both are handled inside the loop rather than once up front:
      - the Android biometric (fingerprint) prompt — Heidi raises it *after* the
        connection screen is accepted, so it must be injected here.
      - the connection-consent screen — trusted ("CONNECT") or untrusted
        ("CONNECT ANYWAY"); ConnectionPage accepts whichever variant appears.

    Returns 'request', 'refused', 'error', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[verification_flow] Biometric prompt — fingerprint injected")
                progress.step("answered the biometric prompt")
                continue
            # Checked before the consent screen: heidi's refusal shares the same
            # "CONNECT WITH:" header but offers only CLOSE.
            if wait_present(driver, connection_refused_id, timeout=1):
                return "refused"
            if wait_present(driver, connection_id, timeout=1):
                logger.info("[verification_flow] Connection consent screen — accepting")
                # heidi's own screen, so it is in front: `open` completed. Without it, a wallet
                # that then handed off elsewhere would be described as never having come forward.
                progress.reached(progress.OPEN, "heidi showed its connection consent screen")
                ConnectionPage(driver).connect()
                progress.step("accepted the connection consent")
                continue
            if wait_present(driver, request_id, timeout=1):
                return "request"
            if (wait_present(driver, error_id, timeout=1)
                    or wait_present(driver, verification_error_id, timeout=1)):
                return "error"
        except WebDriverException as exc:
            # Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record.
            raise lost_session("verification_flow", "mid-wait", exc.msg) from exc
    return "timeout"


def _wait_for_share_result(driver, shared_id, home_id, error_id, verification_error_id,
                           timeout: float):
    """Poll until the success notice, home, or an error screen appears after Share was tapped.

    Heidi raises the fingerprint prompt a *second* time here — "Link Biometric Feature /
    Authenticate yourself to access the credentials" — after the presentation has already been
    sent. Waiting for home directly means that prompt is never answered and the wait expires on
    a screen that belongs to com.android.systemui, which is how a presentation the verifier
    accepted was published as a timeout with no cause.

    Heidi's own confirmation is the "Information Successfully Shared" notice, which it holds
    until DONE is tapped — so home is accepted only as a fallback, not as the expected end.

    Returns 'shared', 'home', 'error', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[verification_flow] Biometric prompt after sharing — "
                            "fingerprint injected")
                progress.step("answered the biometric prompt after sharing")
                continue
            if (wait_present(driver, error_id, timeout=1)
                    or wait_present(driver, verification_error_id, timeout=1)):
                return "error"
            if wait_present(driver, shared_id, timeout=1):
                return "shared"
            if wait_present(driver, home_id, timeout=1):
                return "home"
        except WebDriverException as exc:
            raise lost_session("verification_flow", "after sharing", exc.msg) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the Heidi wallet.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pension_verification")
        app_package:     App package (e.g. "ch.ubique.heidi.android")
        pin:             Unused — Heidi uses biometric, not PIN
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    # No backgrounding — see the note in credential_flow.py. heidi routes the deeplink from the
    # foreground, and the wait loop below is what waits, not a fixed sleep. Nothing here treats
    # the home screen as a verdict, so there is no grace to observe on this path.
    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="information request")

    # _wait_for_request handles the biometric (fingerprint) prompt and the
    # connection-consent screen (trusted or untrusted) internally, so they are
    # caught regardless of the order in which Heidi presents them.
    result = _wait_for_request(
        driver, _request_id, _error_id, _verification_error_id,
        connection_id=_connection_id, connection_refused_id=_connection_refused_id, timeout=t,
    )
    logger.info(f"[verification_flow] Result after {t}s wait: {result}")

    if result == "error":
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[verification_flow] Error screen: {error_text}")
        progress.reached(progress.OPEN, "heidi was in front, showing its error screen")
        # Leave the error screen on display so the failure-artifact capture in
        # teardown dumps the error screen; teardown then navigates back to home.
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] Verification failed for '{credential_name}'{quoted(error_text)}"
        ))

    if result == "refused":
        page = ConnectionPage(driver, **page_args)
        detail = page.refusal_detail()
        logger.error(f"[verification_flow] Connection refused by heidi: {detail}")
        progress.reached(progress.OPEN, "heidi was in front, refusing the connection")
        # A refusal is heidi's verdict on the verifier, so `rejected`. It used to be a plain red
        # cell: no prose pattern matched "refused to connect".
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] heidi refused to connect to the verifier for "
            f"'{credential_name}' — it showed \"A connection cannot be established.\" and "
            f"offered only CLOSE: {detail}"
        ))

    if result == "timeout":
        # This wait never looks for home — heidi is fired from its dashboard, so home is no
        # verdict while it works. At the end it is: still on the dashboard means the request was
        # ignored. Checked here so the shared diagnosis below never says "no home screen" about a
        # wallet that was showing its home screen the whole time.
        if _home_on_screen(driver, timeout=1):
            progress.reached(progress.OPEN, "heidi was in front, on its dashboard")
            raise FlowFailure(outcome.DISMISSED, (
                f"[verification_flow] heidi stayed on its dashboard for {t:.0f}s without showing "
                f"an information request for '{credential_name}' — the deeplink was not processed "
                "or the verifier was rejected silently"
            ))
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="information request",
                          timeout=t)

    progress.reached(progress.PRESENT, "heidi showed the information request")
    if _no_match_on_screen(driver, timeout=2):
        # A non-empty wallet that holds nothing this verifier asked for. An *empty* wallet is the
        # test's `nothing_to_present`, decided before the flow runs.
        outcome.raise_no_match(SCREENS, ctx, said="No Matching Credentials")
    progress.reached(progress.MATCH, "heidi offered a credential to share")

    logger.info("[verification_flow] Information Request screen — sharing credentials")
    VerificationRequestPage(driver, **page_args).share()
    progress.step("tapped Share")

    # After sharing, heidi either shows the "Information Successfully Shared" notice, an error
    # dialog (cert failure, protocol error), or falls back to home — with a fingerprint prompt
    # possibly in between, which heidi raises a second time here. Without the error check the
    # test passes silently despite a failure; without the biometric and the success notice
    # handled inside the loop, a share the verifier accepted reads as a timeout.
    logger.info("[verification_flow] Waiting for heidi to confirm the share")
    result = _wait_for_share_result(
        driver, _shared_id, _home_id, _error_id, _verification_error_id, timeout=t,
    )

    if result == "error":
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[verification_flow] Error screen after sharing: {error_text}")
        # Leave the error screen on display for the teardown failure-artifact dump.
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] Verification failed after sharing '{credential_name}'"
            f"{quoted(error_text)}"
        ))

    if result == "timeout":
        # After Share, so no verdict either way rather than "never came forward".
        raise FlowFailure(outcome.PROCESSING, (
            f"[verification_flow] Heidi neither confirmed nor refused the share of "
            f"'{credential_name}' (waited {t}s). The presentation may have reached the verifier; "
            "the wallet showed no success notice, error or home screen"
        ))

    if result == "shared":
        logger.info("[verification_flow] Information Successfully Shared — dismissing the notice")
        progress.reached(progress.ACCEPT, "heidi showed Information Successfully Shared")
        VerificationSuccessPage(driver, **page_args).dismiss()
    else:
        # Home without heidi's own confirmation: passes, as it always has, but is not evidence
        # that the presentation was handed over — so the stage is not claimed.
        progress.step("heidi returned to its dashboard without confirming the share")

    logger.info(f"[verification_flow] Credential '{credential_name}' shared successfully")
