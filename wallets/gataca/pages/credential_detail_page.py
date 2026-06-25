from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Heading shown when a credential card is opened.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Credential details"]')

# Top action bar has two empty-content-desc clickable ViewGroups: Back (left, 1st) and the
# trash/remove button (right, 2nd). No resource-ids (React Native), so select by order.
_TRASH_BUTTON = (AppiumBy.XPATH,
    '(//android.view.ViewGroup[@clickable="true" and @content-desc=""])[2]'
)

# Confirmation dialog: "Sure you want to remove this credential?" with Cancel / "Yes, delete".
_CONFIRM_DELETE = (AppiumBy.XPATH, '//*[@content-desc="Yes, delete"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Credential details screen did not load within timeout")

    def delete(self):
        """Tap the trash button and confirm 'Yes, delete'.

        This then triggers an Android system biometric prompt; the caller must authenticate
        (e.g. via ``base.android.authenticate_with_pin(driver, pin)``) for the deletion to complete.
        """
        self.click(_TRASH_BUTTON)
        self.click(_CONFIRM_DELETE)
