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
from selenium.webdriver.support.ui import WebDriverWait

from base.utils import wait_present

logger = logging.getLogger(__name__)

# Android system UI package that hosts the biometric prompt bottom-sheet.
SYSTEMUI_PKG = "com.android.systemui"

# The biometric icon is present whenever the fingerprint/face prompt is on screen.
BIOMETRIC_PROMPT = (AppiumBy.ID, "com.android.systemui:id/biometric_icon")

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


def handle_biometric_if_present(driver, dismiss_timeout=10) -> bool:
    """If the Android biometric prompt is on screen, simulate a fingerprint and wait for it to dismiss.

    Args:
        dismiss_timeout: How long to wait (seconds) for the biometric dialog to disappear
                         after simulating the fingerprint. Does not affect the 2s detection probe.

    Returns True if the prompt was detected and handled, False if it was not present.
    Safe to call speculatively — does nothing if the prompt is not showing.
    """
    if not wait_present(driver, BIOMETRIC_PROMPT, timeout=2):
        return False

    logger.info("[android] Biometric prompt detected — simulating fingerprint")
    driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
    WebDriverWait(driver, dismiss_timeout).until(
        lambda d: d.current_package != SYSTEMUI_PKG
    )
    return True


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


def handle_permission_if_present(driver, allow: bool = True) -> bool:
    """If an Android permission dialog is on screen, click Allow or Deny.

    Returns True if handled, False if not present.
    """
    if not wait_present(driver, _PERMISSION_ALLOW_BTN, timeout=0.5):
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
