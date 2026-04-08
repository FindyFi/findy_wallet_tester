from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="SECURITY"]')
ACTIVATION_FAILED_ID = (AppiumBy.XPATH, '//*[@text="Activation Failed"]')

_activate_now = (AppiumBy.XPATH, '//android.view.View[@clickable="true" and .//android.widget.TextView[@text="ACTIVATE NOW"]]')


class SecurityPage(BasePage):
    def activate_now(self):
        self.click(_activate_now)
