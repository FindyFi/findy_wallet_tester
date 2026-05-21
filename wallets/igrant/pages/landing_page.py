from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

# Shared title resource-id used on Welcome, Region, and Default-settings onboarding screens.
# Appears on every onboarding screen except Restore — sufficient to detect "in onboarding".
SCREEN_ID = (AppiumBy.ID, "io.igrant.mobileagent:id/tvWelcome")

# Shared Next/Finish button used on all onboarding screens
_NEXT_BTN = (AppiumBy.ID, "io.igrant.mobileagent:id/btNext")


class LandingPage(BasePage):
    def get_started(self):
        self.click(_NEXT_BTN)
