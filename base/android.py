"""General Android system overlay detection and handling.

These utilities deal with Android system-level UI that can appear on top of any
app at any time — biometric prompts, system dialogs, etc. Import from here in
any flow that needs to react to these overlays rather than blindly proceeding.

Typical usage in a flow:

    overlay = detect_system_overlay(driver)
    if overlay == SystemOverlay.BIOMETRIC_PROMPT:
        handle_biometric_if_present(driver)
    elif overlay == SystemOverlay.APP_CRASH:
        raise RuntimeError("App crashed")
    elif overlay == SystemOverlay.PERMISSION:
        handle_permission_if_present(driver, allow=True)
    elif overlay == SystemOverlay.ANR:
        handle_anr_if_present(driver)
"""
import logging
from enum import Enum
from typing import Optional

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

from base.utils import wait_present

logger = logging.getLogger(__name__)

# Android system UI package that hosts the biometric prompt bottom-sheet.
SYSTEMUI_PKG = "com.android.systemui"

# Settings hosts the *other* shape of device-credential auth: a full-screen ConfirmLockPassword
# ("Re-enter your PIN" / "Enter your device PIN to continue"). Apps get this instead of the
# bottom sheet when they ask for device-credential authentication directly, or when a biometric
# key needs re-confirmation. It takes the **device** PIN, not any app passcode.
SETTINGS_PKG = "com.android.settings"

# The biometric icon is present whenever the fingerprint/face prompt is on screen.
BIOMETRIC_PROMPT = (AppiumBy.ID, "com.android.systemui:id/biometric_icon")

# The system auth prompt appears in different shapes across devices/Android versions: a fingerprint
# sheet (biometric_icon + a "Use PIN" button), or — e.g. Motorola / Android 14 — straight to the
# PIN field (lockPassword) with no fingerprint step. Detect any of these so PIN auth works on real
# phones too, not just the emulator's AOSP SystemUI.
_AUTH_PROMPT = (AppiumBy.XPATH,
    '//*[@resource-id="com.android.systemui:id/biometric_icon"'
    ' or @resource-id="com.android.systemui:id/button_use_credential"'
    ' or @resource-id="com.android.systemui:id/lockPassword"'
    ' or @resource-id="com.android.systemui:id/auth_credential_header"'
    ' or @resource-id="com.android.settings:id/password_entry"]'
)

# "Use PIN" fallback on the fingerprint sheet (by text or by id), and the PIN entry field —
# `lockPassword` on the SystemUI sheet, `password_entry` on Settings' ConfirmLockPassword.
_USE_PIN_BTN = (AppiumBy.XPATH,
    '//*[@text="Use PIN" or @resource-id="com.android.systemui:id/button_use_credential"]'
)
_LOCK_PASSWORD = (AppiumBy.XPATH,
    '//*[@resource-id="com.android.systemui:id/lockPassword"'
    ' or @resource-id="com.android.settings:id/password_entry"]'
)

# "Cancel" on FingerprintEnrollIntroduction (alongside "Setup"); the buttons carry no ids.
_ENROLL_CANCEL = (AppiumBy.XPATH, '//android.widget.Button[@text="Cancel"]')
_KEYCODE_ENTER = 66

# App crash: shown when an app throws an unhandled exception.
_APP_CRASH = (AppiumBy.XPATH, '//*[contains(@text, "has stopped")]')

# ANR (App Not Responding): shown when the app's main thread is blocked.
_ANR = (AppiumBy.XPATH, '//*[contains(@text, "isn\'t responding")]')
_ANR_WAIT_BTN = (AppiumBy.XPATH, '//*[@text="Wait"]')

# Permission request: shown when an app requests a runtime permission.
_PERMISSION_ALLOW_BTN = (AppiumBy.XPATH,
    '//*[contains(@resource-id, "permission_allow_button")'
    ' or @text="Allow" or @text="Allow all the time"'
    ' or @text="Allow only while using the app"'
    ' or @text="While using the app" or @text="Only this time"]'
)
_PERMISSION_DENY_BTN = (AppiumBy.XPATH,
    '//*[contains(@resource-id, "permission_deny_button") or @text="Deny"]'
)


class SystemOverlay(Enum):
    """Known Android system overlays that can appear on top of any app."""
    BIOMETRIC_PROMPT = "biometric_prompt"
    APP_CRASH = "app_crash"
    ANR = "anr"
    PERMISSION = "permission"


def detect_system_overlay(driver) -> Optional[SystemOverlay]:
    """Scan for known Android system overlays and return the first one found.

    Use this when you need to branch on *which* overlay is present. For the
    common case of handling a single overlay type, call the individual handlers
    directly (e.g. ``handle_biometric_if_present``).

    Uses a short probe timeout (0.5s per overlay) so it can be called
    frequently without slowing down the happy path.
    Returns None if no overlay is detected.
    """
    if wait_present(driver, BIOMETRIC_PROMPT, timeout=0.5):
        return SystemOverlay.BIOMETRIC_PROMPT
    if wait_present(driver, _APP_CRASH, timeout=0.5):
        return SystemOverlay.APP_CRASH
    if wait_present(driver, _ANR, timeout=0.5):
        return SystemOverlay.ANR
    if wait_present(driver, _PERMISSION_ALLOW_BTN, timeout=0.5):
        return SystemOverlay.PERMISSION
    return None


def detect_crash_or_anr(driver, timeout: float = 0.3) -> Optional[SystemOverlay]:
    """Just the two overlays that mean the app itself died. None if neither is showing.

    Narrower than `detect_system_overlay` on purpose: that one probes four locators in sequence, so
    at 0.5s each it costs more than a whole poll tick. A wait loop needs to ask this often, and the
    other two overlays it checks (biometric, permission) are things a flow *answers* rather than
    reports — they belong to the interstitial handlers, not to a crash scan.
    """
    if wait_present(driver, _APP_CRASH, timeout=timeout):
        return SystemOverlay.APP_CRASH
    if wait_present(driver, _ANR, timeout=timeout):
        return SystemOverlay.ANR
    return None


def handle_biometric_if_present(driver, dismiss_timeout=10, detect_timeout=2) -> bool:
    """If the Android biometric prompt is on screen, simulate a fingerprint and wait for it to dismiss.

    Args:
        dismiss_timeout: How long to wait (seconds) for the biometric dialog to disappear
                         after simulating the fingerprint.
        detect_timeout: How long to look for the prompt. The 2s default suits a one-shot
                        speculative call; a polling loop that calls this every tick should pass
                        something short, or this single probe costs more than the whole tick.

    Returns True if the prompt was detected and handled, False if it was not present.
    Safe to call speculatively — does nothing if the prompt is not showing.
    """
    if not wait_present(driver, BIOMETRIC_PROMPT, timeout=detect_timeout):
        return False

    logger.info("[android] Biometric prompt detected — simulating fingerprint")
    driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
    WebDriverWait(driver, dismiss_timeout).until(
        lambda d: d.current_package != SYSTEMUI_PKG
    )
    return True


def authenticate_with_pin(driver, pin, detect_timeout=2, dismiss_timeout=10) -> bool:
    """If the Android biometric prompt is on screen, authenticate via PIN instead of fingerprint.

    Taps "Use PIN", types `pin` into the system credential field, and submits with Enter. This
    is preferred over ``handle_biometric_if_present`` for wallets where fingerprint simulation is
    unreliable or locks out ("Biometry is disabled. Please try again in 10 seconds.").

    Args:
        detect_timeout: How long to wait (seconds) for the biometric prompt to appear. Keep the
            short default (2) when calling speculatively (no prompt expected). Pass a longer value
            right after an action that triggers a prompt (e.g. tapping a button), since the system
            bottom-sheet can take a couple of seconds to surface.
        dismiss_timeout: How long to wait for the prompt to dismiss after the PIN is submitted.

    Returns True if a prompt was detected and handled, False if none was present. Safe to call
    speculatively — does nothing if the prompt is not showing.
    """
    if not wait_present(driver, _AUTH_PROMPT, timeout=detect_timeout):
        return False

    logger.info("[android] Auth prompt detected — authenticating with PIN")
    # If the PIN field isn't already visible, tap "Use PIN" to reveal it. Some devices open
    # straight on the PIN screen (no fingerprint sheet), so a missing button is not fatal.
    if not wait_present(driver, _LOCK_PASSWORD, timeout=1):
        try:
            driver.find_element(*_USE_PIN_BTN).click()
        except Exception as e:
            logger.warning(f"[android] 'Use PIN' tap failed: {e}")

    if not wait_present(driver, _LOCK_PASSWORD, timeout=5):
        raise RuntimeError("[android] PIN entry field did not appear after switching to PIN")

    field = driver.find_element(*_LOCK_PASSWORD)
    field.click()
    field.send_keys(str(pin))
    driver.press_keycode(_KEYCODE_ENTER)

    # Settings' ConfirmLockPassword is a full-screen activity, not a SystemUI sheet, so wait
    # for either host to go away rather than just SystemUI.
    try:
        WebDriverWait(driver, dismiss_timeout).until(
            lambda d: d.current_package not in (SYSTEMUI_PKG, SETTINGS_PKG)
        )
    except TimeoutException as exc:
        # A bare timeout here says nothing; name the screen we're stuck on. The common cause
        # is a correct PIN followed by Android's fingerprint *enrollment* wizard.
        raise TimeoutException(
            f"[android] PIN was accepted but the system auth UI did not close within "
            f"{dismiss_timeout}s — still on {_current_screen(driver)}"
            + (". Android is asking to enroll a fingerprint, which means none is enrolled on "
               "this device; enrolling needs a real finger on the sensor."
               if in_biometric_enrollment(driver) else "")
        ) from exc
    return True


def _current_screen(driver) -> str:
    """"package/activity" for diagnostics, or "unknown" if the driver can't say."""
    try:
        return f"{driver.current_package}/{driver.current_activity}"
    except WebDriverException:
        return "unknown"


def dismiss_biometric_enrollment(driver, timeout: float = 5) -> bool:
    """Cancel Android's fingerprint-enrollment offer. Returns True if it was dismissed.

    The wizard is an *upsell*: Android can push it after a successful device-credential
    authentication, and the app's own operation may already have completed underneath it. So
    cancelling and then checking the app's result is more accurate than treating the wizard's
    appearance as failure.
    """
    if not in_biometric_enrollment(driver):
        return False
    logger.info("[android] Fingerprint enrollment offer — cancelling")
    try:
        driver.find_element(*_ENROLL_CANCEL).click()
    except Exception as e:
        logger.warning(f"[android] Could not cancel the enrollment offer: {e}")
        return False
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.current_package != SETTINGS_PKG
        )
    except TimeoutException:
        logger.warning("[android] Enrollment offer did not close after Cancel")
        return False
    return True


def in_biometric_enrollment(driver) -> bool:
    """True if Android opened the fingerprint *enrollment* wizard rather than an auth prompt.

    Being in Settings is not enough to conclude that: ConfirmLockPassword ("Re-enter your
    PIN") is also a Settings activity, and that one is ordinary device-credential auth which
    ``authenticate_with_pin`` satisfies. Only enrollment genuinely can't be automated, since
    it needs a real finger on the sensor — so wallets should report it as a device-setup gap
    rather than a test failure.
    """
    try:
        if driver.current_package != SETTINGS_PKG:
            return False
        return "FingerprintEnroll" in (driver.current_activity or "")
    except WebDriverException:
        return False


def handle_anr_if_present(driver) -> bool:
    """If an ANR dialog is on screen, click Wait to keep the app alive.

    Returns True if handled, False if not present.
    """
    if not wait_present(driver, _ANR, timeout=0.5):
        return False

    logger.warning("[android] ANR dialog detected — clicking Wait")
    try:
        driver.find_element(*_ANR_WAIT_BTN).click()
    except Exception as e:
        logger.warning(f"[android] ANR Wait button click failed: {e}")
    return True


def handle_permission_if_present(driver, allow: bool = True, detect_timeout: float = 0.5) -> bool:
    """If an Android permission dialog is on screen, click Allow or Deny.

    Returns True if handled, False if not present.
    """
    if not wait_present(driver, _PERMISSION_ALLOW_BTN, timeout=detect_timeout):
        return False

    if allow:
        logger.info("[android] Permission dialog detected — clicking Allow")
        try:
            driver.find_element(*_PERMISSION_ALLOW_BTN).click()
        except Exception as e:
            logger.warning(f"[android] Allow button click failed: {e}")
    else:
        logger.info("[android] Permission dialog detected — clicking Deny")
        try:
            driver.find_element(*_PERMISSION_DENY_BTN).click()
        except Exception as e:
            logger.warning(f"[android] Deny button click failed: {e}")
    return True
