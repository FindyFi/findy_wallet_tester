from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_authenticate = (AppiumBy.ACCESSIBILITY_ID, "Authenticate")


def on_screen(driver, timeout=5) -> bool:
    """Return True if the Authorization Code 'Verify your account' screen is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_authenticate))
        return True
    except TimeoutException:
        return False


class AuthenticatePage(BasePage):
    def authenticate(self):
        self.click(_authenticate)
