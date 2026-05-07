from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Login Successful"]')
_OK = (AppiumBy.XPATH, '//*[@content-desc="OK"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class SuccessPage(BasePage):
    def confirm(self):
        self.click(_OK)
