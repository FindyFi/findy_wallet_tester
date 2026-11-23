from appium.webdriver.common.appiumby import AppiumBy

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


def on_screen(driver, timeout=6) -> bool:
    return wait_present(driver, _offer_screen, timeout=timeout) and wait_present(driver, _accept_btn, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_accept_btn)


# ── Processing overlay ─────────────────────────────────────────────────────────
# Shown while toppan stores an accepted credential. No XML dump holds it — passing tests save none,
# and no failure has stopped on it — so screens.py leaves `processing` UNKNOWN. The flow only uses
# it to wait longer, and to say so if it is still up when the wait runs out.

_processing = (AppiumBy.XPATH, '//*[@text="Adding your credential"]')


def processing_on_screen(driver, timeout=1) -> bool:
    return wait_present(driver, _processing, timeout=timeout)
