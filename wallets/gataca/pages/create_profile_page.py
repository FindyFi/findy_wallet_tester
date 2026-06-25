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

    def select_method(self, option_desc: str):
        """Open the DID Method dropdown and select the option by its content-desc.

        `option_desc` is the DID Method label shown in the dropdown (e.g. "JWK", "Gataca",
        "Ebsi Subject"). Selecting it also auto-fills the Alias field accordingly.
        """
        self.click(_DID_METHOD_DROPDOWN)
        self.click((AppiumBy.XPATH, f'//*[@content-desc="{option_desc}"]'))

    def create(self):
        """Tap Create to generate the new profile.

        This triggers an Android system biometric prompt; the caller must authenticate
        (e.g. via ``base.android.authenticate_with_pin(driver, pin)``).
        """
        self.click(_CREATE_BUTTON)
