from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# ── Confirmation dialog ────────────────────────────────────────────────────────
# Appears immediately after a verification deeplink is fired:
#   "Do you want to proceed with verification?"  [CANCEL]  [CONTINUE]

_confirm_screen = (AppiumBy.XPATH, '//*[@text="Do you want to proceed with verification?"]')
_continue_btn   = (AppiumBy.XPATH, '//*[@text="CONTINUE"]')


def confirmation_on_screen(driver, timeout=5) -> bool:
    return wait_present(driver, _confirm_screen, timeout=timeout)


class VerificationConfirmationPage(BasePage):
    def confirm(self):
        self.click(_continue_btn)


# ── Verification request screen ────────────────────────────────────────────────
# Appears after CONTINUE — shows the verifier's credential request.

_request_screen = (AppiumBy.XPATH, '//*[@text="Verification Required"]')
_share_btn      = (AppiumBy.XPATH, '//*[@text="Share"]')


def on_screen(driver, timeout=3) -> bool:
    return wait_present(driver, _request_screen, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        for _ in range(5):
            if wait_present(self.driver, _share_btn, timeout=1):
                break
            self.swipe_up()
        self.click(_share_btn)
