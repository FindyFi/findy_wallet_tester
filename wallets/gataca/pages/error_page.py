from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Shown when Gataca encounters an error processing a deeplink/QR.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Error requesting credentials"]')

_GO_BACK = (AppiumBy.XPATH, '//*[@text="Go back"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class ErrorPage(BasePage):
    def get_error_text(self) -> str:
        try:
            els = self.driver.find_elements(
                AppiumBy.XPATH, '//*[@text and string-length(@text) > 5]'
            )
            return " | ".join(e.get_attribute("text") for e in els if e.get_attribute("text"))
        except Exception:
            return ""

    def go_back(self):
        self.click(_GO_BACK)
