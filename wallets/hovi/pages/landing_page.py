from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Landing screen — uniquely identified by the "Create New Wallet" button.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Create New Wallet"]')

_cta = (AppiumBy.XPATH, '//*[@text="Create New Wallet"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
