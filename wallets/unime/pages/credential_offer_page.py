from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Credential offer screen shown after the wallet receives an issuance deeplink.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Credential Offer"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@text="Accept credentials"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="Reject"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        t = self._get_timeout("credential_offer")
        self.find(_ACCEPT, timeout=t).click()

    def decline(self):
        self.find(_DECLINE).click()
