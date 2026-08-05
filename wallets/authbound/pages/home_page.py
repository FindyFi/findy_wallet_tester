from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable

# EUDI dashboard root — present on every bottom-nav tab once the wallet is open.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_screen_root")

# Bottom-nav "Wallet" tab → the documents (credential list) screen.
_WALLET_TAB = (AppiumBy.ID, "dashboard_screen_bottom_navigation_item_wallet")
_DOCUMENTS_ROOT = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_documents_screen_root")

# TODO (Phase B1): capture the real per-document card locator from a live dump of the Wallet
# tab with a credential present, then implement count_credentials() below. The previous
# placeholder locator matched nothing and the method returned 0, which reported an empty
# wallet as fact — see base/credential_count.py for why that is worse than no count at all.


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Not implemented yet — authbound cannot report a credential count.

        Reaching the list is solved (`_WALLET_TAB` → `_DOCUMENTS_ROOT`); what's missing is the
        per-document card locator, which needs a live dump of the Wallet tab with a credential
        present. Raising keeps that gap visible instead of reporting the wallet as empty.
        """
        raise CredentialCountUnavailable(
            "authbound: the per-document card locator has not been captured yet, so the wallet "
            "cannot report a count (needs a live dump of the Wallet tab with a credential)"
        )
