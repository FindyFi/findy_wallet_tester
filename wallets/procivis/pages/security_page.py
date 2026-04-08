from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

_continue = (AppiumBy.XPATH, '//*[@resource-id="SecurityScreen.continue"]')

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="SecurityScreen"]')


class SecurityPage(BasePage):
    def continue_to_pin(self):
        self.click(_continue)
