from appium.webdriver.common.appiumby import AppiumBy
from base.base_page import BasePage


class ProtectDataPage(BasePage):
    _go_to_wallet = (AppiumBy.ACCESSIBILITY_ID, "Go to wallet")

    def go_to_wallet(self):
        self.click(self._go_to_wallet)
