from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_share = (AppiumBy.ACCESSIBILITY_ID, "Share")


def on_screen(driver, timeout=15) -> bool:
    """Return True if the verification request review screen is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_share))
        return True
    except TimeoutException:
        return False


class VerificationRequestPage(BasePage):
    def share(self):
        self.click(_share)
