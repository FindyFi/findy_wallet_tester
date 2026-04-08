from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Hello!" and @heading="true"]')


class HomePage(BasePage):
    _heading = SCREEN_ID
    _cards_total = (AppiumBy.XPATH, '//*[contains(@text, "card") and contains(@text, "total")]')

    def wait_until_loaded(self, timeout=10):
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(self._heading)
            )
        except TimeoutException:
            raise Exception("Home screen did not load: 'Hello!' heading not found")

    def count_credentials(self) -> int:
        """Return the number of credentials stored in the wallet.

        Reads the 'N cards total' label on the home screen.
        Returns 0 if the wallet is empty (element not present).
        """
        try:
            text = self.driver.find_element(*self._cards_total).text
            return int(text.split()[0])
        except Exception:
            return 0
