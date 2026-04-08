from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Digital credentials simply stored"]')

_cta = (AppiumBy.XPATH, '//android.view.View[@clickable="true" and .//android.widget.TextView[@text="GET STARTED"]]')


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
