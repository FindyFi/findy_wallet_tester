from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

_checkbox = (AppiumBy.XPATH, '//*[@resource-id="UserAgreementScreen.checkbox"]')
_accept = (AppiumBy.XPATH, '//*[@resource-id="UserAgreementScreen.accept"]')

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="UserAgreementScreen"]')


class UserAgreementPage(BasePage):
    def accept(self):
        """Tick the agreement checkbox then tap Accept."""
        self.click(_checkbox)
        self.click(_accept)
