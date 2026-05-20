from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

# TODO: replace with the locator that uniquely identifies the landing/welcome screen
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: landing screen element"]')

# TODO: replace with the locator for the primary CTA button (e.g. "Get Started", "Create wallet")
_cta = (AppiumBy.ACCESSIBILITY_ID, "TODO: CTA button")


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
