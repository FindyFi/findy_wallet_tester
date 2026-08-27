from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Credential offer screen: "Accept" and "Decline" are always present. hovi is React Native, so the
# clickable ViewGroup carries the content-desc while the visible label is a `text` on its child
# TextView. A locator must not require both on one node. Clicking the TextView taps through.
_ACCEPT = (AppiumBy.XPATH, '//*[@text="Accept"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, _ACCEPT, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        t = self._get_timeout("credential_offer")
        self.find(_ACCEPT, timeout=t).click()

    def decline(self):
        self.find(_DECLINE).click()
