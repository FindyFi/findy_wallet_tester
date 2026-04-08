from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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
# TODO: update _share_btn locator once seen with a compatible verifier+credential.
#       Expected text: "Share" or "Accept" (inspect with `python appium.py screen`).

_request_screen = (AppiumBy.XPATH, '//*[@text="OID4VP Verification"]')
_share_btn      = (AppiumBy.XPATH, '//*[@text="Share"]')


def on_screen(driver, timeout=3) -> bool:
    return wait_present(driver, _request_screen, timeout=timeout)


class VerificationRequestPage(BasePage):
    def wait_until_loaded(self, timeout=None):
        t = timeout if timeout is not None else self._get_timeout("default", fallback=10)
        try:
            WebDriverWait(self.driver, t).until(
                EC.presence_of_element_located(_request_screen)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Verification request screen did not appear within {t}s — "
                "check that the deeplink was handled by the app"
            )

    def share(self):
        self.wait_until_loaded()
        self.click(_share_btn)
