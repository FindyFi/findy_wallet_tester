from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

# Home screen is identified by the bottom navigation bar tabs that are always present.
# The greeting text ("What's up, Me.", "How are you, Me.", etc.) changes — do not use it.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Scan"]')

_credential_card = (AppiumBy.XPATH, '//android.widget.Button[starts-with(@text, "img_")]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("UniMe home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards in the wallet.

        Credential cards are android.widget.Button elements whose text starts with "img_" —
        React Native prepends the image's accessibility ID to the button's content
        description, so only credential cards match this pattern. Cards are on home, so no
        navigation is needed, but home has to be showing for zero to mean anything.
        """
        if not on_screen(self.driver, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "unime: home screen is not showing, so a count would mean 'could not look', "
                "not 'wallet is empty'"
            )
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception as e:
            raise CredentialCountUnavailable(f"unime: credential card lookup failed: {e}") from e
