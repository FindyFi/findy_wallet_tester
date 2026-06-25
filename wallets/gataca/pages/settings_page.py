from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# The Settings screen is reached via the bottom-nav "Settings" tab on the home screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Settings"]')

# Bottom-nav Settings tab on the home screen.
SETTINGS_TAB = (AppiumBy.XPATH, '//*[@content-desc="Settings" and @clickable="true"]')

# Sections on the Settings screen.
_PERSONAL_INFORMATION = (AppiumBy.XPATH, '//*[@content-desc="Personal Information"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class SettingsPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Settings screen did not load within timeout")

    def open_personal_information(self):
        self.click(_PERSONAL_INFORMATION)
