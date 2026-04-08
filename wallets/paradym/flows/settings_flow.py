import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from appium.webdriver.common.appiumby import AppiumBy

from wallets.paradym.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)

# Top-left hamburger menu button (first of two "Menu" buttons on the home screen)
_menu_button = (AppiumBy.XPATH, '(//android.widget.Button[@content-desc="Menu"])[1]')

# Each drawer row is a non-clickable ViewGroup containing a clickable Button (icon)
# and a non-clickable View (label text).  Navigate via the label → parent → button.
_settings_nav_item = (AppiumBy.XPATH,
    '//android.view.View[@text="Settings"]/../android.widget.Button')


def _open_settings(driver, timeout):
    """Open the navigation drawer and tap Settings. Raises RuntimeError on timeout."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(_menu_button)
        ).click()
    except TimeoutException:
        raise RuntimeError(
            "[settings_flow] Menu button not found — "
            "ensure the app is on the home screen"
        )

    try:
        WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(_settings_nav_item)
        ).click()
    except TimeoutException:
        raise RuntimeError(
            "[settings_flow] Settings item not found in navigation drawer"
        )


def enable_dev_mode(driver, **page_args):
    """Navigate to Settings, enable Development Mode if it is OFF, then go back.

    Navigation path: home screen → hamburger Menu → Settings item → Settings page.
    After toggling (if needed) presses Android back twice to return to the previous screen.
    The caller is responsible for ensuring the app is on the home screen beforehand,
    and for navigating back to home after this function returns.
    """
    timeout = page_args.get("timeouts", {}).get("default", 10)

    logger.info("[settings_flow] Navigating to Settings")
    _open_settings(driver, timeout)

    settings = SettingsPage(driver, **page_args)
    settings.wait_until_loaded()

    if not settings.is_dev_mode_on():
        logger.info("[settings_flow] Development Mode is OFF — enabling")
        settings.enable_dev_mode()
    else:
        logger.info("[settings_flow] Development Mode is already ON — skipping")

    driver.back()
    driver.back()
    logger.info("[settings_flow] Pressed back from Settings")


def collect_debug_logs(driver, **page_args):
    """Navigate to Settings, export debug logs via the share sheet, return the log text.

    Taps 'Export debug logs', copies the text via the Android share sheet's
    Copy text button, dismisses the share sheet, then reads the clipboard.
    Returns the raw JSON log string.

    Caller must ensure the app is on the home screen before calling.
    """
    timeout = page_args.get("timeouts", {}).get("default", 10)

    logger.info("[settings_flow] Navigating to Settings for log collection")
    _open_settings(driver, timeout)

    settings = SettingsPage(driver, **page_args)
    settings.wait_until_loaded()
    settings.tap_export_debug_logs()
    try:
        settings.copy_logs_to_clipboard()
    finally:
        # Always dismiss share sheet (if open) and exit Settings, even on failure,
        # so the next test doesn't find an open share sheet blocking the UI.
        driver.back()
        driver.back()

    log_text = driver.get_clipboard_text()
    logger.info(f"[settings_flow] Captured {len(log_text)} chars of debug logs")
    return log_text
