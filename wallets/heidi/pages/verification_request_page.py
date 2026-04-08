from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Information Request"]')

_SHARE = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" and .//android.widget.TextView[@text="SHARE INFOS"]]')
_DECLINE = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" and .//android.widget.TextView[@text="DECLINE"]]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        self.find(_SHARE).click()

    def decline(self):
        self.find(_DECLINE).click()
