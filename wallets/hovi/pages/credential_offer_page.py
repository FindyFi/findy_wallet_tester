from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Credential offer screen — "Accept" and "Decline" buttons always present.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Accept" and @clickable="true"] | //*[@text="Decline" and @clickable="true"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@text="Accept"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, (AppiumBy.XPATH, '//*[@text="Accept"]'), timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        t = self._get_timeout("credential_offer")
        self.find(_ACCEPT, timeout=t).click()

    def decline(self):
        self.find(_DECLINE).click()
