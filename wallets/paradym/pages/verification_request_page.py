from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

_share = (AppiumBy.ACCESSIBILITY_ID, "Share")
_close = (AppiumBy.XPATH, '//*[@text="Close"]')

# paradym's own answer when it holds nothing the request asks for: "Review the request" with an
# "UNAVAILABLE CARDS" section, "You don't have the required cards" above a Close button, and no
# Share. 52 dumps, 2026-08-19 to 2026-10-14 — e.g. PUBLISH_findy_wallet_tester/reports/
# 2026-10-14_22-00-01/paradym/xml_dumps/
# test_credential_verification_procivis_verifier_pension_verification-paradym.xml.
NO_MATCH_TEXT = "You don't have the required cards"
_no_cards = (AppiumBy.XPATH, f'//*[@text="{NO_MATCH_TEXT}"]')


def on_screen(driver, timeout=15) -> bool:
    """Return True if the verification request review screen is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(_share))
        return True
    except TimeoutException:
        return False


def no_match(driver, timeout: float = 2) -> bool:
    """paradym showed the request and says it does not have the cards it asks for."""
    return wait_present(driver, _no_cards, timeout=timeout)


def no_match_now(driver) -> bool:
    """Whether the no-match review screen is up right now: one lookup, no wait. For teardown, which
    meets the screen already rendered."""
    return bool(driver.find_elements(*_no_cards))


def close_no_match(driver) -> bool:
    """Close the no-match review screen through its own Close, if it is up. Returns True if
    tapped. Never raises. For teardown: the flow leaves the screen up as the failure evidence."""
    try:
        if not no_match_now(driver):
            return False
        driver.find_element(*_close).click()
        return True
    except Exception:
        return False


class VerificationRequestPage(BasePage):
    def share(self):
        self.click(_share)
