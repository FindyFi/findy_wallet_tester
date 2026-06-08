import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# Passcode/unlock screen title — text reads "Enter Passcode".
HEADING = (AppiumBy.ID, "io.authbound.wallet:id/quick_pin_title")


def on_screen(driver, timeout=2) -> bool:
    """Return True if a PIN entry screen is currently visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(HEADING))
        return True
    except TimeoutException:
        return False


class PinPage(BasePage):
    def _digit(self, d: str):
        return (AppiumBy.ID, f"io.authbound.wallet:id/quick_pin_digit_{d}")

    def enter_pin(self, pin: str):
        delay = self._get_timeout("pin_digit_delay", 0.3)
        for digit in pin:
            self.click(self._digit(digit))
            time.sleep(delay)
