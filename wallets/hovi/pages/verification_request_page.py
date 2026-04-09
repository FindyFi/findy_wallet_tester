from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Verification request screen — identified by "You have received an information request from"
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="You have received an information request from"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@text="Accept"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        self.find(_ACCEPT).click()

    def decline(self):
        self.find(_DECLINE).click()
