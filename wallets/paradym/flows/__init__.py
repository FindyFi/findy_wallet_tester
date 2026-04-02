import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.android import SystemOverlay, detect_system_overlay
from base.utils import wait_present

logger = logging.getLogger(__name__)

# Success screen shown after a credential is issued or a verification is completed.
GO_TO_WALLET = (AppiumBy.XPATH, '//*[contains(@content-desc, "Go to wallet")]')

_ERROR_HEADING = (AppiumBy.XPATH, '//*[@text="Something went wrong"]')
_ERROR_REASON = (AppiumBy.XPATH, '//*[starts-with(@text, "Reason:")]')


def check_for_error(driver, step: str):
    """Raise RuntimeError if a system or app-level error is detected.

    Checks for Android crash/ANR overlays and Paradym's 'Something went wrong'
    screen.  Call this between flow steps to surface failures quickly rather
    than waiting for a later timeout to expire.

    Args:
        driver: Appium driver
        step:   Human-readable label included in the error message (e.g. "after consent")
    """
    overlay = detect_system_overlay(driver)
    if overlay == SystemOverlay.APP_CRASH:
        raise RuntimeError(f"[{step}] App crashed — check logcat for details")
    if overlay == SystemOverlay.ANR:
        raise RuntimeError(f"[{step}] App not responding (ANR)")

    if wait_present(driver, _ERROR_HEADING, timeout=1):
        reason = ""
        try:
            reason = " " + driver.find_element(*_ERROR_REASON).text
        except Exception:
            pass
        raise RuntimeError(f"[{step}] Paradym error screen.{reason}")
