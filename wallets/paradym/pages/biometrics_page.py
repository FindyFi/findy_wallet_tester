from appium.webdriver.common.appiumby import AppiumBy
from base.base_page import BasePage


class BiometricsPage(BasePage):
    _skip = (AppiumBy.ACCESSIBILITY_ID, "Set up later")

    def skip(self):
        self.click(self._skip)
