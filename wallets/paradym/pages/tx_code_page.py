from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_heading = (AppiumBy.XPATH, '//*[@text="Enter transaction code"]')
_input   = (AppiumBy.CLASS_NAME, "android.widget.EditText")


def on_screen(driver, timeout=5) -> bool:
    """Return True if the transaction code entry screen is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_heading))
        return True
    except TimeoutException:
        return False


class TxCodePage(BasePage):
    def enter_code(self, code: str):
        """Type the transaction code into the focused input field.

        The field auto-submits once the expected number of digits are entered
        (max-text-length=4 for ITB flows) — no submit button is needed.
        """
        self.find(_input).send_keys(code)
