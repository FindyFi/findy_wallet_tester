from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# TODO: the presentation-request / share screens could NOT be captured yet — the authbound
# wallet aborts at its auth/profile gate ("User not authenticated") and the wallet is empty
# (no credential to present), so the request screen does not render. Fill these in once the
# wallet has a valid authenticated profile AND holds a matching credential.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: verification request heading"]')

# Shown when the verifier asks for a credential the wallet does not hold.
NO_MATCHING_CREDENTIALS_ID = (AppiumBy.XPATH, '//*[@text="TODO: no matching credentials"]')

_SHARE = (AppiumBy.XPATH, '//*[@text="TODO: share button"]')
_DECLINE = (AppiumBy.XPATH, '//*[@text="TODO: decline button"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        self.click(_SHARE)

    def decline(self):
        self.click(_DECLINE)
