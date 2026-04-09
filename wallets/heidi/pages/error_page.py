import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

# The "An error occurred." heading is unique to Heidi's error screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="An error occurred."]')

# The CANCEL button: inner TextView has clickable="false"; parent View is clickable="true".
_CANCEL = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="CANCEL"]]')

# All TextViews inside the ScrollView that carry meaningful error detail.
_ERROR_TEXTS = (AppiumBy.XPATH, '//android.widget.ScrollView//android.widget.TextView')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class ErrorPage(BasePage):
    def get_error_text(self) -> str:
        """Collect all non-trivial text from the error ScrollView."""
        try:
            elements = self.driver.find_elements(*_ERROR_TEXTS)
            texts = [
                el.get_attribute("text")
                for el in elements
                if el.get_attribute("text") and len(el.get_attribute("text")) > 3
            ]
            return " | ".join(texts)
        except Exception:
            return "<could not read error text>"

    def cancel(self):
        self.find(_CANCEL).click()
