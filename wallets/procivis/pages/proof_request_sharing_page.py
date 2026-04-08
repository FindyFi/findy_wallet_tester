from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestSharingScreen"]')
_share = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestSharingScreen.shareButton"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class ProofRequestSharingPage(BasePage):
    def share(self):
        self.swipe_up()
        self.find(_share).click()
