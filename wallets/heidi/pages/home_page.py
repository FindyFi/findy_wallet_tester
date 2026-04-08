from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# The credentials count label ("0 Credentials", "1 Credentials", etc.) is unique to the home screen.
# ("WALLET" also appears in Settings/About, so we use "Credentials" as the anchor.)
SCREEN_ID = (AppiumBy.XPATH, '//*[contains(@text, "Credentials")]')


# The settings button is the rightmost header button (second Button sibling after "WALLET").
_SETTINGS_BTN = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="WALLET"]'
    '/following-sibling::android.widget.Button[last()]'
)


class HomePage(BasePage):
    def open_settings(self):
        self.click(_SETTINGS_BTN)

    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")
