from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# UniMe uses a password (not a digit PIN).  The lock screen heading is "Unlock wallet".
HEADING = (AppiumBy.XPATH, '//*[@text="Unlock wallet"]')

# Password input on the lock screen.
_password_input = (AppiumBy.XPATH, '//android.widget.EditText[@hint="Enter your password"]')

# The submit button also says "Unlock wallet".
_unlock_btn = (AppiumBy.XPATH, '//android.widget.Button[@text="Unlock wallet"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, HEADING, timeout=timeout)


class PinPage(BasePage):
    """Handles the UniMe password lock screen.  Called 'PinPage' for interface compatibility."""

    def enter_pin(self, pin: str):
        """Type the password and submit."""
        self.find(_password_input).click()
        self.driver.execute_script("mobile: type", {"text": pin})
        self.find(_unlock_btn).click()
