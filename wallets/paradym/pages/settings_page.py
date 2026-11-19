import re
from typing import Optional

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from base.base_page import BasePage

# Found on paradym 1.23.0 (nightly 2026-11-11_22-00-03 appium.log, 02:46:14).
_settings_heading = (AppiumBy.XPATH, '//*[@text="Settings" and @heading="true"]')

# The clickable toggle row next to the "Development Mode" label.
#
# The label is matched on any element class. Through paradym 1.20.2 it was an
# `android.widget.TextView`, and that locator both read and tapped the toggle. On 1.22.3 and 1.23.0
# it returns 404 (nightly 2026-11-11_22-00-03 appium.log, 02:46:11-02:46:14: the Menu button, the
# drawer's Settings item and the Settings heading were all found, this locator was not), and the
# TextView-anchored "Export debug logs" row stopped matching in the same runs — which suggests the
# Settings labels are no longer TextViews. The class-agnostic form below is UNVERIFIED: no dump of
# the Settings screen exists, so a device run has to confirm that it matches.
_dev_mode_toggle = (AppiumBy.XPATH,
    '//*[@text="Development Mode"]'
    '/following-sibling::android.view.ViewGroup[@clickable="true"][1]')

# The inner thumb element whose x-position reveals ON/OFF state
_dev_mode_thumb = (AppiumBy.XPATH,
    '//*[@text="Development Mode"]'
    '/following-sibling::android.view.ViewGroup[@clickable="true"][1]'
    '/android.view.ViewGroup')


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

    def is_dev_mode_on(self) -> Optional[bool]:
        """True or False from the toggle's thumb position, None when it cannot be read.

        The thumb sits on the right half of the track when ON, on the left half when OFF. A toggle
        that is missing or has unparseable bounds reads as None rather than OFF: guessing OFF would
        have the caller tap a toggle that may be ON and switch Development Mode off. A dead session
        is not "cannot be read" and propagates.
        """
        try:
            outer = self.driver.find_element(*_dev_mode_toggle)
            thumb = self.driver.find_element(*_dev_mode_thumb)

            ox1, _, ox2, _ = _parse_bounds(outer.get_attribute("bounds"))
            tx1, _, tx2, _ = _parse_bounds(thumb.get_attribute("bounds"))
        except (NoSuchElementException, ValueError, IndexError, TypeError):
            return None

        return (tx1 + tx2) / 2 > (ox1 + ox2) / 2

    def enable_dev_mode(self):
        """Tap the Development Mode toggle."""
        self.click(_dev_mode_toggle)
