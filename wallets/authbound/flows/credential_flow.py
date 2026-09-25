import logging
import time as _time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.android import (
    authenticate_with_pin,
    dismiss_biometric_enrollment,
    handle_biometric_if_present,
    in_biometric_enrollment,
)
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.authbound.pages.home_page import SCREEN_ID as _home_id
from wallets.authbound.pages.pin_page import PinPage, HEADING as _pin_heading
from wallets.authbound.pages.error_page import ErrorPage, SCREEN_ID as _error_id, quoted
from wallets.authbound.pages.document_success_page import (
    DocumentSuccessPage,
    wait_for_outcome,
)
from wallets.authbound.pages.credential_offer_page import (
    CredentialOfferPage,
    SCREEN_ID as _offer_id,
)
from wallets.authbound.screens import SCREENS

logger = logging.getLogger(__name__)


_ENROLLMENT_REQUIRED = (
    "[credential_flow] Cannot complete issuance of '{what}': Android opened its fingerprint "
    "enrollment wizard, which means no biometric is enrolled on this device (check with "
    "`adb shell dumpsys fingerprint` — \"count\":0 means none). Enrolling needs a real finger "
    "on the sensor, so no test can pass this; enroll one by hand and the wallet shows a normal "
    "authentication prompt here instead. Note that changing the device lock PIN wipes existing "
    "enrollments [no_retry]"
)


def _wait_for_result(driver, pin: str, page_args: dict, timeout: float) -> str:
    """Poll until the error, offer, or home screen appears.

    Handles two interstitials inline (they can appear in any order):
      - the Android biometric (fingerprint) prompt,
      - the app passcode screen (if the wallet re-locks on resume) — entered with `pin`.

    Returns 'error', 'offer', 'home', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if handle_biometric_if_present(driver):
                logger.info("[credential_flow] Biometric prompt — fingerprint injected")
                progress.step("answered the biometric prompt")
                continue
            if wait_present(driver, _pin_heading, timeout=1):
                logger.info("[credential_flow] Passcode screen — entering PIN")
                PinPage(driver, **page_args).enter_pin(pin)
                progress.step("entered the wallet passcode")
                continue
            if wait_present(driver, _error_id, timeout=1):
                return "error"
            if wait_present(driver, _offer_id, timeout=1):
                return "offer"
            if wait_present(driver, _home_id, timeout=1):
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
    """Open a credential offer deeplink and accept it in the authbound (EUDI) wallet.

    NOTE on backgrounding: unlike some wallets, authbound processes the offer via
    onNewIntent only when it is already in the foreground. Pressing HOME first makes the
    deeplink merely resume the existing task ("brought to the front") WITHOUT delivering
    the offer, so we fire the deeplink directly while the app is open.

    Tapping "Add" hands off to Android for authentication, which arrives as either the SystemUI
    biometric sheet (with a "Use PIN" fallback) or Settings' ConfirmLockPassword — both take the
    **device** PIN, not the wallet passcode.

    On a device with no fingerprint enrolled, Android also pushes its enrollment wizard, often
    *after* accepting the PIN. That offer is dismissed rather than treated as the verdict,
    because the wallet has been observed storing the document underneath it (2026-08-10: a run
    that reported failure had in fact added the credential). The wallet's own success/error
    screen decides, and the enrollment gap is only reported when neither appears.

    This supersedes two earlier diagnoses: that the wallet rejected offers at an auth/profile
    gate before any consent screen, and that reaching the enrollment wizard necessarily meant
    the issuance had failed.
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # Android refused to route it into the app. The shared diagnosis asks the device which
        # schemes authbound registers, so `unroutable` is only claimed with that evidence.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="credential offer")
    _time.sleep(3)

    result = _wait_for_result(driver, pin, page_args, timeout=t)
    logger.info(f"[credential_flow] Result (waited up to {t}s): {result}")

    if result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[credential_flow] Error screen: {error_text}")
        progress.reached(progress.OPEN, "authbound was in front, showing its error screen")
        # Leave the error screen up so teardown's failure-artifact capture dumps it;
        # init_flow navigates home afterwards.
        raise FlowFailure(outcome.REJECTED, (
            f"[credential_flow] Credential issuance failed for '{credential_name}'"
            f"{quoted(error_text)}"
        ))

    if result == "offer":
        logger.info("[credential_flow] Credential offer screen — accepting")
        progress.reached(progress.PRESENT, "authbound showed the credential offer")
        CredentialOfferPage(driver, **page_args).accept()
        progress.step("tapped Add")

        # "Add" delegates to Android for authentication. This arrives in one of two shapes —
        # the SystemUI biometric sheet (with a "Use PIN" fallback) or Settings'
        # ConfirmLockPassword — and both take the **device** PIN, not the wallet's passcode.
        # authenticate_with_pin handles both.
        _time.sleep(2)
        device_pin = page_args.get("device_pin", "")
        saw_enrollment = False
        try:
            if authenticate_with_pin(driver, device_pin, detect_timeout=10):
                logger.info("[credential_flow] Device authentication completed with PIN")
                progress.step("answered device authentication with the PIN")
            elif handle_biometric_if_present(driver):
                logger.info("[credential_flow] Biometric prompt — fingerprint injected")
                progress.step("answered the biometric prompt")
        except Exception as exc:
            # The PIN is usually accepted and Android *then* pushes its enrollment offer, which
            # makes authenticate_with_pin's "auth UI never closed" wait fail. That says nothing
            # about whether the wallet stored the document, so don't treat it as the verdict.
            if not in_biometric_enrollment(driver):
                raise
            saw_enrollment = True
            logger.warning(
                "[credential_flow] PIN accepted, then Android offered fingerprint enrollment"
            )

        # Dismiss the offer rather than concluding from it: it is an upsell, and the wallet has
        # been observed storing the document underneath it. The outcome below is the real answer.
        if in_biometric_enrollment(driver):
            saw_enrollment = True
            dismiss_biometric_enrollment(driver)

        # The wallet either stores the document or lands on its generic error screen; waiting
        # only for success would report an issuer-side failure as a missing success screen.
        result_after = wait_for_outcome(driver, _error_id, timeout=t, device_pin=device_pin)

        if result_after == "error":
            error_text = ErrorPage(driver, **page_args).get_error_text()
            raise FlowFailure(outcome.REJECTED, (
                f"[credential_flow] Credential issuance failed for '{credential_name}' after "
                f"authentication{quoted(error_text)}"
            ))

        if result_after == "prompt_loop":
            raise FlowFailure(outcome.PROMPT_LOOP, (
                f"[credential_flow] '{credential_name}' could not be stored: the wallet kept "
                "re-requesting device authentication — every PIN was accepted and a fresh prompt "
                "appeared immediately, with no success or error screen. This is what a signing "
                "key that requires BIOMETRIC_STRONG looks like: the device-credential PIN "
                "satisfies Android's prompt but does not unlock the key, so a real fingerprint "
                "touch on the sensor may be the only thing that completes it [no_retry]"
            ))

        if result_after != "success":
            # No success and no error. If Android hijacked the flow with its enrollment offer,
            # that is the likeliest reason and it needs a human — a precondition of the device,
            # not a verdict on the wallet. Otherwise the wait ran out with no verdict either way.
            if saw_enrollment:
                raise FlowFailure(outcome.HARNESS, _ENROLLMENT_REQUIRED.format(what=credential_name))
            # `processing`, not `rejected`: the prose reading of this sentence filed it as a
            # rejection because it contains the words "error screen".
            raise FlowFailure(outcome.PROCESSING, (
                f"[credential_flow] '{credential_name}' was accepted and authenticated, but "
                f"neither the success nor the error screen appeared within {t}s"
            ))

        progress.reached(progress.ACCEPT, "authbound showed its success screen")
        success = DocumentSuccessPage(driver, **page_args)
        added = success.added_document_names()
        logger.info(
            f"[credential_flow] Credential '{credential_name}' stored — wallet added: "
            f"{added or '(name not readable)'}"
        )
        # Close the success screen so the caller resumes on the dashboard.
        success.close()
        return

    if result == "home":
        progress.reached(progress.OPEN, "authbound was in front, on its dashboard")
        raise FlowFailure(outcome.DISMISSED, (
            f"[credential_flow] App returned to home without showing an offer screen for "
            f"'{credential_name}' — the deeplink was not processed or the issuer rejected silently"
        ))

    # Nothing recognisable: the shared diagnosis checks the device's registered schemes before
    # blaming the wallet, and says which of authbound's surfaces it looked for.
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="credential offer", timeout=t)
