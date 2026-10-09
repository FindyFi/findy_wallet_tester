import logging
import time as _time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.android import handle_biometric_if_present
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.heidi.pages.home_page import SCREEN_ID as _home_id
from wallets.heidi.pages.connection_page import (
    ConnectionPage,
    SCREEN_ID as _connection_id,
    REFUSED_ID as _connection_refused_id,
)
from wallets.heidi.pages.error_page import ErrorPage, SCREEN_ID as _error_id, quoted
from wallets.heidi.pages.credential_offer_page import (
    CredentialOfferPage,
    SCREEN_ID as _offer_id,
)
from wallets.heidi.screens import SCREENS

logger = logging.getLogger(__name__)

# How long after firing the deeplink the home screen is NOT taken as a silent rejection.
#
# heidi is on its home screen when the deeplink fires, so without this the loop would read that
# very screen and report "issuer rejected silently" before heidi had a chance to react. This
# replaced a blanket `sleep(10)` after the deeplink: the grace bounds only the one ambiguous
# verdict, while every other screen is still acted on the moment it appears.
_HOME_GRACE = 8.0


def _wait_for_result(driver, error_id, offer_id, home_id, connection_id,
                     connection_refused_id, timeout: float, home_grace: float = _HOME_GRACE):
    """Poll until the error, offer, or home screen appears.

    Two interstitial screens can appear mid-wait, in either order and more than
    once, so both are handled inside the loop rather than once up front:
      - the Android biometric (fingerprint) prompt — Heidi raises it *after* the
        connection screen is accepted, so it must be injected here; otherwise the
        wait times out on the fingerprint screen and never reaches the
        error/offer screen.
      - the connection-consent screen — trusted ("CONNECT") or untrusted
        ("CONNECT ANYWAY"); ConnectionPage accepts whichever variant appears.

    Returns 'error', 'refused', 'offer', 'home' (issuer rejected silently), or 'timeout'.
    """
    start = _time.time()
    end = start + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[credential_flow] Biometric prompt — fingerprint injected")
                progress.step("answered the biometric prompt")
                continue
            # Checked before the consent screen: heidi's refusal shares the same
            # "CONNECT WITH:" header but offers only CLOSE, so the consent branch would
            # try to accept a screen with nothing to accept.
            if wait_present(driver, connection_refused_id, timeout=1):
                return "refused"
            if wait_present(driver, connection_id, timeout=1):
                logger.info("[credential_flow] Connection consent screen — accepting")
                # heidi's own screen, so it is in front: `open` completed. Without it, a wallet
                # that then handed off elsewhere would be described as never having come forward.
                progress.reached(progress.OPEN, "heidi showed its connection consent screen")
                ConnectionPage(driver).connect()
                progress.step("accepted the connection consent")
                continue
            if wait_present(driver, error_id, timeout=1):
                return "error"
            if wait_present(driver, offer_id, timeout=1):
                return "offer"
            if wait_present(driver, home_id, timeout=1):
                # Home is only a verdict once the grace has passed — before that it is just the
                # screen heidi was already on when the deeplink fired.
                if _time.time() - start < home_grace:
                    continue
                return "home"
        except WebDriverException as exc:
            # Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record.
            raise FlowFailure(outcome.LOST_SESSION, (
                f"[credential_flow] Appium/UiAutomator2 connection lost mid-wait "
                f"(app may have crashed): {exc.msg}"
            )) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Heidi wallet.

    Handles:
      - "Untrusted Connection" consent screen
      - Android biometric (fingerprint) prompt
      - "Add Credential" offer screen — accepts the credential
      - Generic error screen ("An error occurred.") — fails as `rejected`, quoting heidi
      - Silent rejection — app returns to home without showing any UI
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 60))

    # No backgrounding. heidi used to be pressed HOME first, on the belief that it would not
    # process a deeplink while in the foreground; measured on 2026-09-03 it routes fine either
    # way, reaching the connection screen in ~8s from the foreground. The HOME press plus its two
    # `sleep(10)`s cost 20s of every issuance test and bought nothing.
    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="credential offer")

    # _wait_for_result handles the biometric (fingerprint) prompt and the
    # connection-consent screen (trusted or untrusted) internally, so they are
    # caught regardless of the order in which Heidi presents them.
    result = _wait_for_result(
        driver, _error_id, offer_id=_offer_id, home_id=_home_id,
        connection_id=_connection_id, connection_refused_id=_connection_refused_id, timeout=t,
    )
    logger.info(f"[credential_flow] Result after {t}s wait: {result}")

    if result == "error":
        error_page = ErrorPage(driver, **page_args)
        error_text = error_page.get_error_text()
        logger.error(f"[credential_flow] Error screen: {error_text}")
        progress.reached(progress.OPEN, "heidi was in front, showing its error screen")
        # Leave the error screen on display so the failure-artifact capture in
        # teardown dumps the error screen (not the screen after a CANCEL tap).
        # Teardown's init_flow navigates back to home afterwards, dismissing it.
        raise FlowFailure(outcome.REJECTED, (
            f"[credential_flow] Credential issuance failed for '{credential_name}'"
            f"{quoted(error_text)}"
        ))

    if result == "refused":
        page = ConnectionPage(driver, **page_args)
        detail = page.refusal_detail()
        logger.error(f"[credential_flow] Connection refused by heidi: {detail}")
        progress.reached(progress.OPEN, "heidi was in front, refusing the connection")
        # A refusal is heidi's verdict on the issuer, so `rejected`. It used to be a plain red
        # cell: no prose pattern matched "refused to connect".
        raise FlowFailure(outcome.REJECTED, (
            f"[credential_flow] heidi refused to connect to the issuer for '{credential_name}' — "
            f"it showed \"A connection cannot be established.\" and offered only CLOSE: {detail}"
        ))

    if result == "offer":
        logger.info("[credential_flow] Credential offer screen — accepting")
        progress.reached(progress.PRESENT, "heidi showed the credential offer")
        CredentialOfferPage(driver, **page_args).accept()
        # heidi shows no confirmation of its own; the test's count is what completes `keep`.
        progress.step("tapped Add")
        logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
        return

    if result == "home":
        progress.reached(progress.OPEN, "heidi was in front, on its dashboard")
        raise FlowFailure(outcome.DISMISSED, (
            f"[credential_flow] App returned to home without showing an offer screen for "
            f"'{credential_name}' — issuer may have rejected silently or the deeplink was not processed"
        ))

    # Nothing recognisable: the shared diagnosis checks the device's registered schemes before
    # blaming the wallet, and says which of heidi's surfaces it looked for.
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="credential offer", timeout=t)
