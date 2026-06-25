from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Manage advanced features"]')

# The Advanced toggle is a clickable ViewGroup sibling of the "Advanced" row label.
# There are two "Advanced" text nodes on this page (title + row); the row label is the one
# followed by a clickable sibling ViewGroup.
_ADVANCED_TOGGLE = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Advanced"]/following-sibling::android.view.ViewGroup[@clickable="true"]'
)

# After enabling Advanced, a "Multi DID" link appears. Tap it to open Multi DID settings.
_MULTI_DID_LINK = (AppiumBy.XPATH, '//*[@content-desc="Multi DID" and @clickable="true"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class AdvancedPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Advanced screen did not load within timeout")

    def has_multi_did_link(self) -> bool:
        """Return True if the Multi DID row is visible (meaning Advanced is already on)."""
        return wait_present(self.driver, _MULTI_DID_LINK, timeout=2)

    def toggle_advanced(self):
        """Tap the Advanced toggle switch."""
        self.click(_ADVANCED_TOGGLE)

    def open_multi_did(self):
        self.click(_MULTI_DID_LINK)
