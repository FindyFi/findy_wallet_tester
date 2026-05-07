import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# TODO: replace with the heading text shown on the PIN entry screen.
# Flutter apps typically expose text via content-desc; try AppiumBy.ACCESSIBILITY_ID if
# AppiumBy.XPATH with @text doesn't find it.
HEADING = (AppiumBy.XPATH, '//*[@text="TODO: sphereon PIN screen heading"]')


def on_screen(driver, timeout=2) -> bool:
    """Return True if a PIN entry screen is currently visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(HEADING))
        return True
    except TimeoutException:
        return False


class PinPage(BasePage):
    def _digit(self, d: str):
        # TODO: adjust the locator to match sphereon PIN digit buttons.
        # Flutter apps often use content-desc; e.g. (AppiumBy.ACCESSIBILITY_ID, d)
        return (AppiumBy.ACCESSIBILITY_ID, f"TODO: sphereon pin button {d}")

    def enter_pin(self, pin: str):
        delay = self._get_timeout("pin_digit_delay", 0.3)
        for digit in pin:
            self.click(self._digit(digit))
            time.sleep(delay)
