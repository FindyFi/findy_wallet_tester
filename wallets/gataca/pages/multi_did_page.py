from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# The Multi DID toggle row — always present regardless of whether the feature is on or off.
# The description text "Turn on multi DID feature..." only appears when the toggle is OFF,
# so it cannot be used as a reliable screen identifier.
SCREEN_ID = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Multi DID"]/following-sibling::android.view.ViewGroup[@clickable="true"]'
)

# The Multi DID toggle — same relative locator pattern as AdvancedPage's toggle.
_MULTI_DID_TOGGLE = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Multi DID"]/following-sibling::android.view.ViewGroup[@clickable="true"]'
)

# "My Aliases:" section appears once Multi DID is toggled on.
_MY_ALIASES_HEADER = (AppiumBy.XPATH, '//*[@text="My Aliases:"]')

# An alias entry; each alias has a visible label (e.g. "Gataca", "Ebsi Identity").
# Ebsi Identity is created automatically by the Create Profile dialog when "Ebsi Subject"
# is chosen as the DID method.
_EBSI_IDENTITY_ALIAS = (AppiumBy.XPATH, '//*[@text="Ebsi Identity"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class MultiDidPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca Multi DID screen did not load within timeout")

    def is_enabled(self) -> bool:
        """Return True if Multi DID feature is on (aliases section is visible)."""
        return wait_present(self.driver, _MY_ALIASES_HEADER, timeout=2)

    def toggle_multi_did(self):
        self.click(_MULTI_DID_TOGGLE)

    def has_ebsi_alias(self) -> bool:
        """Return True if an 'Ebsi Identity' alias already exists."""
        return wait_present(self.driver, _EBSI_IDENTITY_ALIAS, timeout=2)
