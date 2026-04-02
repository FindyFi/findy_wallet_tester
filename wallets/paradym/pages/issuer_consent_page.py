from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_continue = (AppiumBy.ACCESSIBILITY_ID, "Yes, continue")


def on_screen(driver, timeout=3) -> bool:
    """Return True if the issuer interaction consent screen is currently visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_continue))
        return True
    except TimeoutException:
        return False


class IssuerConsentPage(BasePage):
    def confirm(self):
        self.click(_continue)
