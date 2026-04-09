from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Home screen is identified by the "Scan QR" bottom nav button which is always present.
# The heading alternates between "This is your wallet" (empty) and "Credentials" (has creds).
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Scan QR" or @text="This is your wallet" or @text="Credentials"]')

# Credential cards are ViewGroup elements with a non-empty content-desc on the home screen.
# Each card has content-desc like ", EläKeläIstodiste, Issuer, Findynet".
_credential_card = (AppiumBy.XPATH,
    '//android.view.ViewGroup[contains(@content-desc,"Issuer")]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Hovi home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards visible on the home screen."""
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception:
            return 0
