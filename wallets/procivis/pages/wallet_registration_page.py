from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

_error_indicator = (AppiumBy.XPATH, '//*[@resource-id="WalletUnitRegistrationScreen.animation.warning"]')
_close = (AppiumBy.XPATH, '//*[@resource-id="WalletUnitRegistrationScreen.close"]')

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="WalletUnitRegistrationScreen"]')


class WalletRegistrationPage(BasePage):
    def wait_for_completion(self):
        """Wait for wallet registration to complete.

        Success path: screen disappears automatically (navigates to home).
        Error path: tap Close immediately to reach home.
        """
        t = self._get_timeout("default")
        try:
            WebDriverWait(self.driver, t).until(
                EC.invisibility_of_element_located(SCREEN_ID)
            )
            return  # Registered successfully
        except TimeoutException:
            pass

        # Still on screen — tap Close to dismiss and proceed to home
        self.click(_close)
