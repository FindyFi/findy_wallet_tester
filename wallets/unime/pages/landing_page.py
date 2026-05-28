from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Landing/welcome screen — "Create new profile" button uniquely identifies it.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Create new profile"]')

_cta = (AppiumBy.XPATH, '//*[@text="Create new profile"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
