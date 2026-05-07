from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Your DID"]')

# The DID value (starts with "did:...") — the current active DID on the wallet.
_DID_VALUE = (AppiumBy.XPATH, '//*[starts-with(@text, "did:")]')

# Row linking to the Advanced page. Its content-desc is "Advanced" and it's clickable.
_ADVANCED_ROW = (AppiumBy.XPATH, '//*[@content-desc="Advanced" and @clickable="true"]')

# "+" button in the top-right action bar. Has empty content-desc and no text; the back button
# shares the same signature, so we pick the second Button with empty content-desc
# (back = 1st, "+" = 2nd). Only appears when Multi DID is enabled.
_ADD_PROFILE_BUTTON = (AppiumBy.XPATH, '(//android.widget.Button[@content-desc=""])[2]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class PersonalInfoPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Personal Information screen did not load within timeout")

    def current_did(self) -> str:
        """Return the currently active DID string shown on Personal Information."""
        try:
            el = self.driver.find_element(*_DID_VALUE)
            return el.get_attribute("text") or ""
        except Exception:
            return ""

    def is_ebsi_active(self) -> bool:
        """Return True if the active DID is a did:key (EBSI Subject)."""
        return self.current_did().startswith("did:key:")

    def open_advanced(self):
        """Tap the Advanced row to open the Advanced settings page."""
        self.click(_ADVANCED_ROW)

    def open_create_profile(self):
        """Tap the "+" button in the top-right to open the Create Profile dialog.
        Only available when Multi DID is enabled."""
        self.click(_ADD_PROFILE_BUTTON)
