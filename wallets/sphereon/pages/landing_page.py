from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

# TODO: replace with the locator that uniquely identifies the landing/welcome screen.
# Run: python .../appium.py screen   after first launch to find candidate elements.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: sphereon landing screen element"]')

# TODO: replace with the locator for the primary CTA button (e.g. "Get Started", "Create Wallet")
_cta = (AppiumBy.ACCESSIBILITY_ID, "TODO: sphereon CTA button")


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
