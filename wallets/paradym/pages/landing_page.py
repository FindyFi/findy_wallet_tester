from appium.webdriver.common.appiumby import AppiumBy
from base.base_page import BasePage

SCREEN_ID = (AppiumBy.ACCESSIBILITY_ID, "Get Started")


class LandingPage(BasePage):
    def get_started(self):
        self.click(SCREEN_ID)
