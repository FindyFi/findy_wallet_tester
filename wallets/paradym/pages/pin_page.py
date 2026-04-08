import time
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# Heading shown on the unlock/login screen (after onboarding is complete).
# Used by flows to detect whether the app is locked.
HEADING = (AppiumBy.XPATH, '//*[@text="Enter your app PIN code" or @text="Send data with your PIN code"]')


def on_screen(driver, timeout=2) -> bool:
    """Return True if the PIN unlock screen is currently visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(HEADING))
        return True
    except TimeoutException:
        return False


class PinPage(BasePage):
    def _digit(self, d: str):
        return (AppiumBy.ACCESSIBILITY_ID, f"Pin number {d}")

    def enter_pin(self, pin: str):
        delay = self._get_timeout("pin_digit_delay", 0.3)
        for digit in pin:
            self.click(self._digit(digit))
            time.sleep(delay)
