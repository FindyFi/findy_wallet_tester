from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Home screen is identified by the bottom navigation bar tabs that are always present.
# The greeting text ("What's up, Me.", "How are you, Me.", etc.) changes — do not use it.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Scan"]')


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
        """Return the number of credential cards currently visible in the wallet.

        UniMe shows credentials as clickable Button elements in the home screen list.
        Each credential card is a Button with a non-empty text (the credential name).
        We exclude navigation buttons ("Add", "Me", "Scan", "Activity") by checking
        that the button is inside the credential list area (not the bottom nav).
        """
        try:
            # Credential cards are android.widget.Button elements with non-empty text
            # that appear in the main content area above the bottom navigation.
            # Filter to buttons that are not part of the bottom nav (which uses small icons).
            buttons = self.driver.find_elements(
                "xpath",
                '//android.widget.Button[string-length(@text) > 0 '
                'and not(@text="Add") '
                'and not(@text="Me") '
                'and not(@text="Scan") '
                'and not(@text="Activity")]'
            )
            return len(buttons)
        except Exception:
            return 0
