from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# UniMe verification consent screen.
# Title observed on OID4VP presentation request screens.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Verification Request"]')

_SHARE = (AppiumBy.XPATH, '//*[@text="Share credentials"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="Reject"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        self.find(_SHARE).click()

    def decline(self):
        self.find(_DECLINE).click()
