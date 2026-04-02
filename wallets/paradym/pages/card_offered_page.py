from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_continue = (AppiumBy.XPATH, '//*[@content-desc="Continue"]')


def on_screen(driver, timeout=5) -> bool:
    """Return True if the 'Card offered' screen (with a Continue button) is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_continue))
        return True
    except TimeoutException:
        return False


class CardOfferedPage(BasePage):
    def continue_(self):
        self.click(_continue)
