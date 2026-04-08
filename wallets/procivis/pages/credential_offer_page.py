import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="CredentialOfferScreen"]')
_accept = (AppiumBy.XPATH, '//*[@resource-id="CredentialOfferScreen.accept"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def wait_until_loaded(self, timeout=None):
        self.find(SCREEN_ID, timeout=timeout)

    def accept(self):
        # Wait for the Accept button to exist in the DOM (may be off-screen) before swiping,
        # so we know the screen is fully rendered and not still loading.
        t = self._get_timeout("credential_offer")
        self.find(_accept, timeout=t)
        logger.info("[credential_offer] Swiping to reveal Accept button")
        self.swipe_up()
        self.find(_accept).click()
