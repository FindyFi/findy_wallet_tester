from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="PRIVACY"]')

_continue = (AppiumBy.XPATH, '//android.view.View[@clickable="true" and .//android.widget.TextView[@text="CONTINUE"]]')


class PrivacyPage(BasePage):
    def continue_onboarding(self):
        self.click(_continue)
