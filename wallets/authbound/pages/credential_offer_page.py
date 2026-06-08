from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# TODO: the offer/consent/accept screens could NOT be captured yet — the authbound
# wallet aborts credential offers at its auth/profile gate ("User not authenticated")
# before the offer screen renders, landing on error_page instead. Fill these in once the
# wallet has a valid authenticated profile and a real offer reaches this screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: credential offer heading"]')
_ACCEPT = (AppiumBy.XPATH, '//*[@text="TODO: accept button"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="TODO: decline button"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT)

    def decline(self):
        self.click(_DECLINE)
