from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

_create_wallet = (AppiumBy.ACCESSIBILITY_ID, "Create new wallet")

# Used for screen detection without instantiating the page
SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="OnboardingSetupScreen"]')


class LandingPage(BasePage):
    def create_new_wallet(self):
        self.click(_create_wallet)
