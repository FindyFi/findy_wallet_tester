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
        # Accept button is at the bottom of a scrollable list and is not rendered in the DOM
        # until scrolled into view (lazy rendering). Swipe to the bottom first, then find+click.
        t = self._get_timeout("credential_offer")
        logger.info("[credential_offer] Swiping to reveal Accept button")
        self.swipe_up()
        self.swipe_up()
        self.find(_accept, timeout=t).click()
