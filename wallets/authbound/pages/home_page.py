from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# EUDI dashboard root — present on every bottom-nav tab once the wallet is open.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_screen_root")

# Bottom-nav "Wallet" tab → the documents (credential list) screen.
_WALLET_TAB = (AppiumBy.ID, "dashboard_screen_bottom_navigation_item_wallet")
_DOCUMENTS_ROOT = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_documents_screen_root")

# TODO (Phase B1): replace with the real per-document card locator captured live once a
# credential has been issued. Until then count_credentials() falls back to 0.
_DOCUMENT_CARD = (AppiumBy.XPATH, '//*[@text="TODO: document card"]')


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credentials (documents) currently in the wallet.

        Navigates to the Wallet tab and counts document cards. The per-card locator is
        still a placeholder (confirmed live in Phase B1), so this safely returns 0 until
        then rather than erroring on an empty/unknown layout.
        """
        try:
            self.click(_WALLET_TAB)
            self.find(_DOCUMENTS_ROOT)
            return len(self.driver.find_elements(*_DOCUMENT_CARD))
        except Exception:
            return 0
