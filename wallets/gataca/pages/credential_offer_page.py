from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Gataca uses the same "Sharing requirements" screen for both issuance and verification.
# Issuance: user presents email credential to prove identity; server then issues the credential.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Sharing requirements"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@content-desc="Share"]')
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT)

    def decline(self):
        self.click(_DECLINE)
