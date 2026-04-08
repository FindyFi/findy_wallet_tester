import re
import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

logger = logging.getLogger(__name__)

_settings_heading = (AppiumBy.XPATH, '//*[@text="Settings" and @heading="true"]')

# The clickable toggle row next to the "Development Mode" label
_dev_mode_toggle = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Development Mode"]'
    '/following-sibling::android.view.ViewGroup[@clickable="true"][1]')

# The inner thumb element whose x-position reveals ON/OFF state
_dev_mode_thumb = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Development Mode"]'
    '/following-sibling::android.view.ViewGroup[@clickable="true"][1]'
    '/android.view.ViewGroup')

# Export debug logs row — only visible when Developer Mode is ON
_export_logs_row = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Export debug logs"]/..')

# Copy button in the Android share sheet. Resource ID is stable across Android versions.
_copy_text_button = (AppiumBy.ID, 'android:id/chooser_copy_button')


def _parse_bounds(bounds_str: str) -> tuple:
    """Parse '[x1,y1][x2,y2]' → (x1, y1, x2, y2)."""
    nums = list(map(int, re.findall(r'\d+', bounds_str)))
    return nums[0], nums[1], nums[2], nums[3]


class SettingsPage(BasePage):
    def wait_until_loaded(self, timeout=None):
        t = timeout if timeout is not None else self._get_timeout("default")
        try:
            WebDriverWait(self.driver, t).until(
                EC.presence_of_element_located(_settings_heading)
            )
        except TimeoutException:
            raise RuntimeError("Settings screen did not load — heading not found")

    def is_dev_mode_on(self) -> bool:
        """Return True if the Development Mode toggle is enabled.

        Detection: the toggle thumb sits on the right half of the track when ON,
        and on the left half when OFF.
        """
        try:
            outer = self.driver.find_element(*_dev_mode_toggle)
            thumb = self.driver.find_element(*_dev_mode_thumb)

            ox1, _, ox2, _ = _parse_bounds(outer.get_attribute("bounds"))
            tx1, _, tx2, _ = _parse_bounds(thumb.get_attribute("bounds"))

            outer_center = (ox1 + ox2) / 2
            thumb_center = (tx1 + tx2) / 2

            return thumb_center > outer_center
        except Exception:
            logger.warning("[settings_page] Could not read dev mode toggle state — assuming OFF")
            return False

    def enable_dev_mode(self):
        """Tap the Development Mode toggle to switch it ON."""
        self.click(_dev_mode_toggle)
        logger.info("[settings_page] Tapped Development Mode toggle")

    def tap_export_debug_logs(self, timeout=None):
        """Tap the Export debug logs row to trigger the Android share sheet."""
        t = timeout if timeout is not None else self._get_timeout("default")
        try:
            WebDriverWait(self.driver, t).until(
                EC.element_to_be_clickable(_export_logs_row)
            ).click()
            logger.info("[settings_page] Tapped Export debug logs")
        except TimeoutException:
            raise RuntimeError(
                "[settings_page] Export debug logs row not found — is Developer Mode enabled?"
            )

    def copy_logs_to_clipboard(self, timeout=None):
        """Wait for the share sheet and tap the Copy text button."""
        t = timeout if timeout is not None else self._get_timeout("default")
        try:
            WebDriverWait(self.driver, t).until(
                EC.element_to_be_clickable(_copy_text_button)
            ).click()
            logger.info("[settings_page] Tapped Copy text in share sheet")
        except TimeoutException:
            raise RuntimeError(
                "[settings_page] Share sheet Copy text button not found"
            )
