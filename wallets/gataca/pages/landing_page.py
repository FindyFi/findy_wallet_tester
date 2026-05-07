from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="The place for your digital identities"]')

_CREATE = (AppiumBy.XPATH, '//*[@content-desc="Create ID Wallet"]')
_OPEN = (AppiumBy.XPATH, '//*[@content-desc="Open your wallet"]')


def has_open_wallet_button(driver, timeout: float = 2) -> bool:
    """Return True if this is a returning-user landing (Open your wallet), not fresh install."""
    return wait_present(driver, _OPEN, timeout=timeout)


class LandingPage(BasePage):
    def open_wallet(self):
        """Tap 'Open your wallet' — for returning users who need to re-authenticate."""
        self.click(_OPEN)

    def get_started(self):
        """Tap 'Create ID Wallet' — for fresh installs requiring email + OTP onboarding."""
        self.click(_CREATE)
