from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# ── Confirmation dialog ────────────────────────────────────────────────────────
# Appears immediately after the deeplink is fired:
#   "Do you want to proceed with OpenID4VCI issuance?"  [CANCEL]  [CONTINUE]

_confirm_screen = (AppiumBy.XPATH, '//*[@text="Do you want to proceed with OpenID4VCI issuance?"]')
_continue_btn   = (AppiumBy.XPATH, '//*[@text="CONTINUE"]')


def confirmation_on_screen(driver, timeout=5) -> bool:
    return wait_present(driver, _confirm_screen, timeout=timeout)


class ConfirmationPage(BasePage):
    def confirm(self):
        self.click(_continue_btn)


# ── Credential offer modal ─────────────────────────────────────────────────────
# Appears after CONTINUE is tapped (may take a moment to load from issuer):
#   "OID4VCI Issuance / <issuer> wants to add the following / <credential>"
#   [Reject]  [Accept]

_offer_screen = (AppiumBy.XPATH, '//*[@text="OID4VCI Issuance"]')
_accept_btn   = (AppiumBy.XPATH, '//*[@text="Accept"]')


def on_screen(driver, timeout=3) -> bool:
    return wait_present(driver, _offer_screen, timeout=timeout)


class CredentialOfferPage(BasePage):
    def wait_until_loaded(self, timeout=None):
        t = timeout if timeout is not None else self._get_timeout("credential_offer", fallback=30)
        try:
            WebDriverWait(self.driver, t).until(
                EC.presence_of_element_located(_offer_screen)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Credential offer screen ('OID4VCI Issuance') did not appear within {t}s — "
                "check that the deeplink was handled by the app"
            )

    def accept(self):
        self.wait_until_loaded()
        self.click(_accept_btn)
