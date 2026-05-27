from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TOPPAN Wallet"]')



class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards on the home screen.

        Counts occurrences in the raw page source rather than find_elements so
        that cards scrolled out of the viewport are included.
        """
        try:
            return self.driver.page_source.count('Issued on')
        except Exception:
            return 0
