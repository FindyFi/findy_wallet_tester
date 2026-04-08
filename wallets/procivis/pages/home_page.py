from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="WalletScreen.header"]')


class HomePage(BasePage):
    def wait_until_loaded(self, timeout=None):
        self.find(SCREEN_ID, timeout=timeout)
