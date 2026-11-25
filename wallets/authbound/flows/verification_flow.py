import logging
import time as _time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.android import (
    authenticate_with_pin,
    handle_biometric_if_present,
    in_biometric_enrollment,
)
from base.flow_context import FlowContext
from base.flow_failures import lost_session
from base.outcome import FlowFailure
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.authbound.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.authbound.pages.pin_page import PinPage, HEADING as _pin_heading
from wallets.authbound.pages.error_page import ErrorPage, SCREEN_ID as _error_id, quoted
from wallets.authbound.pages.document_success_page import (
    DocumentSuccessPage,
    wait_for_outcome,
)
from wallets.authbound.pages.verification_request_page import (
    VerificationRequestPage,
    SCREEN_ID as _request_id,
    no_match as _no_match_on_screen,
)
from wallets.authbound.screens import SCREENS

logger = logging.getLogger(__name__)


def _authenticated_since(tap: str) -> bool:
    """Whether the trail recorded an authentication prompt answered after the step `tap` — by this
    flow or by `wait_for_outcome`. Messages used to say "after authentication" unconditionally; on
    2026-09-25 authbound rejected every offer ~16 s after Add with no prompt at all.

    What the trail does not record, this does not claim: the negative wording is "no authentication
    recorded", not "none happened".
    """
    return any(step.startswith("answered ") for step in progress.steps_after(tap))


# The openid4vp:// scheme is registered by many wallets on the device, so firing a
# verifier deeplink can raise Android's app-chooser (ResolverActivity). Select Authbound
# (and "Just once") to route the request into this wallet.
_CHOOSER_ITEM = (AppiumBy.XPATH, '//*[@text="Authbound Wallet"]')
_CHOOSER_ONCE = (AppiumBy.XPATH, '//*[@text="Just once"]')

# The step recorded when Share is tapped; authentication is only claimed if answered after it.
_TAPPED_STEP = "tapped Share"

_ENROLLMENT_REQUIRED = (
    "[verification_flow] Cannot complete sharing for '{what}': Android opened its fingerprint "
    "enrollment wizard, which means no biometric is enrolled on this device (check with "
    "`adb shell dumpsys fingerprint` — \"count\":0 means none). Enrolling needs a real finger on "
    "the sensor, so no test can pass this; enroll one by hand. Note that changing the device "
    "lock PIN wipes existing enrollments [no_retry]"
)


def _wait_for_request(driver, pin: str, page_args: dict, timeout: float) -> str:
    """Poll until the presentation-request, error, or home screen appears.

    Handles interstitials inline (any order):
      - Android's app-chooser (multiple wallets share the openid4vp scheme),
      - the Android biometric (fingerprint) prompt,
      - the app passcode screen (if the wallet re-locks on resume) — entered with `pin`.

    Returns 'request', 'error', 'home', or 'timeout'.
    """
    end = _time.time() + timeout
    while _time.time() < end:
        try:
            if wait_present(driver, _CHOOSER_ITEM, timeout=1):
                logger.info("[verification_flow] App chooser — selecting Authbound Wallet")
                driver.find_element(*_CHOOSER_ITEM).click()
                if wait_present(driver, _CHOOSER_ONCE, timeout=2):
                    driver.find_element(*_CHOOSER_ONCE).click()
                progress.step("picked Authbound Wallet in Android's app chooser")
                continue
            if handle_biometric_if_present(driver):
                logger.info("[verification_flow] Biometric prompt — fingerprint injected")
                progress.step("answered the biometric prompt")
                continue
            if wait_present(driver, _pin_heading, timeout=1):
                logger.info("[verification_flow] Passcode screen — entering PIN")
                PinPage(driver, **page_args).enter_pin(pin)
                progress.step("entered the wallet passcode")
                continue
            if wait_present(driver, _error_id, timeout=1):
                return "error"
            if wait_present(driver, _request_id, timeout=1):
                return "request"
            if wait_present(driver, _home_id, timeout=1):
                return "home"
        except WebDriverException as exc:
            # Ours, not the wallet's: a dead UiAutomator2 session is kept off the vendor's record.
            raise lost_session("verification_flow", "mid-wait", exc.msg) from exc
    return "timeout"


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the authbound (EUDI) wallet.

    NOTE on backgrounding: like issuance, authbound processes the deeplink via onNewIntent only
    when it is already in the foreground — pressing HOME first only resumes the task without
    delivering the request, so we fire the deeplink directly.

    The request screen's locators were captured live 2026-08-05 (`request_screen_root`,
    `request_screen_button`, and the `request_screen_empty_state` shown when the verifier asks
    for something the wallet doesn't hold). Sharing, like accepting an offer, hands off to
    Android for authentication — the device PIN, not the wallet's passcode.

    The wallet presents **every** matching document and demands a separate authentication for
    each: measured live 2026-08-12, six listed documents needed seven authentications before it
    completed. So the prompt budget is derived from the request screen's document count rather
    than assumed. Because that count grows as the wallet accumulates credentials, sharing looked
    like it "worked once and then broke" — a one-credential wallet needed a single prompt.

    This supersedes two earlier notes: that verification was blocked by an auth/profile gate,
    and that a repeating prompt meant a key requiring BIOMETRIC_STRONG.
    """
    ctx = FlowContext(
        flow="verification_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    t = timeouts.get("credential_offer", timeouts.get("default", 30))

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # Android refused to route it into the app. The shared diagnosis asks the device which
        # schemes authbound registers, so `unroutable` is only claimed with that evidence.
        logger.info(f"[verification_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="information request")
    _time.sleep(3)

    result = _wait_for_request(driver, pin, page_args, timeout=t)
    logger.info(f"[verification_flow] Result (waited up to {t}s): {result}")

    if result == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[verification_flow] Error screen: {error_text}")
        progress.reached(progress.OPEN, "authbound was in front, showing its error screen")
        # Leave the error screen up so teardown's failure-artifact capture dumps it.
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] Verification failed for '{credential_name}'"
            f"{quoted(error_text)}"
        ))

    if result == "home":
        progress.reached(progress.OPEN, "authbound was in front, on its dashboard")
        raise FlowFailure(outcome.DISMISSED, (
            f"[verification_flow] App returned to home without showing a request screen for "
            f"'{credential_name}' — the deeplink was not processed or the verifier rejected silently"
        ))

    if result == "timeout":
        # Nothing recognisable: the shared diagnosis checks the device's registered schemes
        # before blaming the wallet, and says which of authbound's surfaces it looked for.
        outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="information request",
                          timeout=t)

    # result == "request"
    progress.reached(progress.PRESENT, "authbound showed the presentation request")
    # Visibility, not presence: the request screen may keep its empty-state panel in the
    # hierarchy even when a document matches, and a presence check would then abort every
    # verification with a false "no matching credentials".
    if _no_match_on_screen(driver, timeout=2):
        # Raised directly rather than through `outcome.raise_no_match`, to keep authbound's own
        # hint and the `[no_retry]` it has always carried: the batch does not refill on a retry.
        raise FlowFailure(outcome.NO_MATCH, (
            f"[verification_flow] No matching credentials for '{credential_name}' — the wallet "
            "reports it holds nothing this verifier asked for. Check the credential's remaining "
            "presentation instances: authbound issues a batch, and a credential showing "
            "'0/1 instances remaining' cannot be presented again [no_retry]"
        ))

    request_page = VerificationRequestPage(driver, **page_args)

    # The wallet presents every matching document and demands a separate device authentication
    # for each, so budget the prompts from what it is actually about to share (+2 headroom: the
    # first prompt precedes the per-document ones). Measured live 2026-08-12: 6 documents → 7
    # authentications. This number grows with the wallet's contents.
    documents = request_page.requested_document_count()
    logger.info(
        f"[verification_flow] Presentation request screen — sharing {documents} document(s); "
        f"expect up to {documents + 2} authentication prompts"
    )
    if documents:
        progress.reached(progress.MATCH, f"authbound listed {documents} document(s) to share")
    else:
        # The count's locator can find nothing on a screen that does hold a match, so zero is
        # not evidence either way: recorded without claiming the stage.
        progress.step("authbound's document list could not be counted")
    request_page.share()
    progress.step(_TAPPED_STEP)

    # Sharing requires device authentication, same as accepting an offer: either the SystemUI
    # biometric sheet or Settings' ConfirmLockPassword, both taking the **device** PIN.
    _time.sleep(2)
    device_pin = page_args.get("device_pin", "")
    try:
        if authenticate_with_pin(driver, device_pin, detect_timeout=10):
            logger.info("[verification_flow] Device authentication completed with PIN")
            progress.step("answered device authentication with the PIN")
        elif handle_biometric_if_present(driver):
            logger.info("[verification_flow] Biometric prompt — fingerprint injected")
            progress.step("answered the biometric prompt")
    except Exception as exc:
        # The PIN is often accepted and Android *then* opens the enrollment wizard, so this has
        # to be checked on the failure path too — not only when auth never started.
        if in_biometric_enrollment(driver):
            # A precondition of the device, not a verdict on the wallet.
            raise FlowFailure(outcome.SETUP,
                              _ENROLLMENT_REQUIRED.format(what=credential_name)) from exc
        raise

    if in_biometric_enrollment(driver):
        raise FlowFailure(outcome.SETUP, _ENROLLMENT_REQUIRED.format(what=credential_name))

    # Sharing ends on the wallet's success screen ("You successfully shared the following…"),
    # the same screen issuance ends on — not on the dashboard. Waiting for home here reported
    # a successful presentation as a failure.
    result_after = wait_for_outcome(driver, _error_id, timeout=t, device_pin=device_pin,
                                    max_prompts=max(documents + 2, 4))

    if result_after == "error":
        error_text = ErrorPage(driver, **page_args).get_error_text()
        logger.error(f"[verification_flow] Error screen after sharing: {error_text}")
        raise FlowFailure(outcome.REJECTED, (
            f"[verification_flow] Verification failed after sharing '{credential_name}'"
            f"{quoted(error_text)}"
        ))

    if result_after == "prompt_loop":
        raise FlowFailure(outcome.PROMPT_LOOP, (
            f"[verification_flow] '{credential_name}' could not be shared: the wallet kept "
            "re-requesting device authentication — every PIN was accepted and a fresh prompt "
            "appeared immediately, with no success or error screen. This is what a signing "
            "key that requires BIOMETRIC_STRONG looks like: the device-credential PIN "
            "satisfies Android's prompt but does not unlock the key, so a real fingerprint "
            "touch on the sensor may be the only thing that completes it [no_retry]"
        ))

    if result_after != "success":
        # `processing`, not `rejected`: the prose reading of this sentence filed it as a rejection
        # because it contains the words "error screen".
        how = ("shared and authenticated" if _authenticated_since(_TAPPED_STEP)
               else "shared, with no authentication recorded after Share,")
        raise FlowFailure(outcome.PROCESSING, (
            f"[verification_flow] '{credential_name}' was {how} but neither the success nor the "
            f"error screen appeared within {t}s"
        ))

    progress.reached(progress.ACCEPT, "authbound showed its success screen")
    success = DocumentSuccessPage(driver, **page_args)
    shared = success.added_document_names()
    logger.info(
        f"[verification_flow] Credential '{credential_name}' shared successfully — presented: "
        f"{shared or '(name not readable)'}"
    )
    # Close the success screen so the caller resumes on the dashboard.
    success.close()
    HomePage(driver, **page_args).wait_until_loaded()
