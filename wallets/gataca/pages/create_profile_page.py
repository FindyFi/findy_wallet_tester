from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Dialog heading — appears when the "+" button is tapped on Personal Information.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Create Profile"]')

# The DID Method dropdown wrapper. Its label is "DID Method"; the row below contains
# the currently-selected method (default "Gataca") and is clickable.
_DID_METHOD_DROPDOWN = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="DID Method"]/following-sibling::android.view.ViewGroup[@clickable="true"]'
)

# Dropdown options that appear after tapping the DID Method dropdown.
_OPTION_EBSI_SUBJECT = (AppiumBy.XPATH, '//*[@content-desc="Ebsi Subject"]')

# The "Create" button at the bottom of the dialog (content-desc is "Create").
_CREATE_BUTTON = (AppiumBy.XPATH, '//*[@content-desc="Create" and @clickable="true"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CreateProfilePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Create Profile dialog did not load within timeout")

    def select_ebsi_subject(self):
        """Open the DID Method dropdown and select 'Ebsi Subject'.

        Selecting this also auto-fills the Alias field to 'Ebsi Identity'.
        """
        self.click(_DID_METHOD_DROPDOWN)
        self.click(_OPTION_EBSI_SUBJECT)

    def create(self):
        """Tap Create to generate the new profile.

        This triggers an Android system biometric prompt; the caller must authenticate
        (e.g. via ``driver.execute_script("mobile: fingerprint", ...)``).
        """
        self.click(_CREATE_BUTTON)
