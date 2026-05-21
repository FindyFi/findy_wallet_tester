from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# TODO: replace with the locator that uniquely identifies the credential offer screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: credential offer heading"]')

# TODO: replace with the locator for the Accept / Add button.
_ACCEPT = (AppiumBy.XPATH, '//*[@text="TODO: accept button"]')

# TODO: replace with the locator for the Decline / Cancel button.
_DECLINE = (AppiumBy.XPATH, '//*[@text="TODO: decline button"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT)

    def decline(self):
        self.click(_DECLINE)
