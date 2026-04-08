import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.android import SystemOverlay, detect_system_overlay
from base.utils import wait_present

logger = logging.getLogger(__name__)

# Toppan shows a standard Android AlertDialog on errors (e.g. issuer rejection).
# The OK button always has this system resource-id.
_ERROR_OK_BTN = (AppiumBy.ID, "android:id/button1")

# Processing overlay that appears while the credential is being stored.
PROCESSING = (AppiumBy.XPATH, '//*[@text="Adding your credential"]')


def check_for_error(driver, step: str):
    """Raise RuntimeError if a system or app-level error is detected.

    Checks for Android crash/ANR overlays and Toppan's error AlertDialog
    (identified by the system OK button, android:id/button1).
    """
    overlay = detect_system_overlay(driver)
    if overlay == SystemOverlay.APP_CRASH:
        raise RuntimeError(f"[{step}] App crashed — check logcat for details")
    if overlay == SystemOverlay.ANR:
        raise RuntimeError(f"[{step}] App not responding (ANR)")

    if wait_present(driver, _ERROR_OK_BTN, timeout=1):
        try:
            msg = driver.find_element(
                AppiumBy.ID, "android:id/message"
            ).text
        except Exception:
            msg = "(unknown)"
        raise RuntimeError(f"[{step}] Toppan error dialog: {msg}")
