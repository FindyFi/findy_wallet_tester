import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# TODO: replace with the locator that uniquely identifies the home screen
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: home screen heading"]')

# TODO: replace with the locator for the credential count label (e.g. "3 credentials").
# If no count label exists, implement count_credentials() by counting list items directly.
_credential_count = (AppiumBy.XPATH, '//*[contains(@text, "TODO: credential count label")]')


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credentials currently in the wallet.

        TODO: implement once the home screen layout is known.
        Options:
          - Parse a count label: re.search(r'(\\d+)', el.get_attribute("text"))
          - Count list items: len(driver.find_elements(*_card_locator))
        """
        try:
            el = self.find(_credential_count)
            m = re.search(r'(\d+)', el.get_attribute("text") or "")
            return int(m.group(1)) if m else 0
        except Exception:
            return 0
