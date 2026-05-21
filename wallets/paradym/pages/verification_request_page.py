from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_share = (AppiumBy.ACCESSIBILITY_ID, "Share")
_no_cards = (AppiumBy.XPATH, '//*[@text="You don\'t have the required cards"]')
_close = (AppiumBy.XPATH, '//*[@text="Close"]')


def on_screen(driver, timeout=15) -> bool:
    """Return True if the verification request review screen is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_share))
        return True
    except TimeoutException:
        return False


def has_unavailable_cards(driver) -> bool:
    """Return True if the review screen shows cards the wallet doesn't have."""
    try:
        driver.find_element(*_no_cards)
        return True
    except Exception:
        return False


class VerificationRequestPage(BasePage):
    def share(self):
        self.click(_share)

    def close(self):
        self.click(_close)
