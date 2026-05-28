from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Home screen is identified by the bottom navigation bar tabs that are always present.
# The greeting text ("What's up, Me.", "How are you, Me.", etc.) changes — do not use it.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Scan"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("UniMe home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards currently visible in the wallet.

        Credential cards are android.widget.Button elements whose text starts with
        "img_" — React Native prepends the image's accessibility ID to the button's
        content description, so only credential cards match this pattern.
        """
        try:
            buttons = self.driver.find_elements(
                "xpath",
                '//android.widget.Button[starts-with(@text, "img_")]'
            )
            return len(buttons)
        except Exception:
            return 0
