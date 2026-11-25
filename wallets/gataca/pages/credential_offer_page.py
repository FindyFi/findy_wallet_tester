from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Gataca uses the same "Sharing requirements" screen for both issuance and verification.
# Issuance: user presents email credential to prove identity; server then issues the credential.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Sharing requirements"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@content-desc="Share"]')
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Decline"]')

# "Email: Unavailable" indicator — shown when the active DID has no email credential.
_EMAIL_UNAVAILABLE = (AppiumBy.XPATH, '//*[@text="Unavailable"]')

# gataca's answer when the wallet holds nothing that satisfies the request: a dialog laid OVER the
# Sharing requirements screen, so "Sharing requirements" and Share are still in the tree beneath
# it (nine archived dumps, gataca 2.5.9, 2026-08-27..09-24, e.g.
# 2026-08-27_18-46-59/gataca/xml_dumps/test_credential_verification_hovi_verifier_*-ebsi.xml).
# Probe it BEFORE the offer. Not seen yet in 2.6.5; if the wording changed this never fires.
NO_MATCH_TEXT = "Unfortunately, giving consent is not possible"
_NO_MATCH = (AppiumBy.XPATH, f'//*[@text="{NO_MATCH_TEXT}"]')
_NO_MATCH_BODY = (AppiumBy.XPATH, '//*[starts-with(@text, "You are unable to answer the consent")]')

# The overlay gataca leaves over its screens while it submits the consent (seen over Sharing
# requirements in 2026-06-15 dumps). In front, and still working.
_PROCESSING = (AppiumBy.XPATH, '//*[@text="Sending your consent"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def no_match(driver, timeout: float = 2) -> bool:
    """gataca's "giving consent is not possible" dialog (missing required credentials)."""
    return wait_present(driver, _NO_MATCH, timeout=timeout)


def no_match_text(driver) -> str:
    """The dialog's explanation ("…missing required credentials…"), or its heading. Never raises."""
    try:
        found = driver.find_elements(*_NO_MATCH_BODY)
        if found:
            return " ".join((found[0].get_attribute("text") or "").split())
    except Exception:
        pass
    return NO_MATCH_TEXT


def processing(driver, timeout: float = 2) -> bool:
    """"Sending your consent" — gataca is working on the answer."""
    return wait_present(driver, _PROCESSING, timeout=timeout)


def email_unavailable(driver, timeout: float = 2) -> bool:
    """The offer's "Email: Unavailable" — the active DID holds no email credential."""
    return wait_present(driver, _EMAIL_UNAVAILABLE, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT)

    def decline(self):
        self.click(_DECLINE)
