from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import logging

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

logger = logging.getLogger(__name__)

# EUDI dashboard root — present on every bottom-nav tab once the wallet is open.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_screen_root")

# Bottom-nav tabs. Counting goes to Wallet and must come back to Home.
_WALLET_TAB = (AppiumBy.ID, "dashboard_screen_bottom_navigation_item_wallet")
_HOME_TAB = (AppiumBy.ID, "dashboard_screen_bottom_navigation_item_home")
_DOCUMENTS_ROOT = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_documents_screen_root")

# Empty state of the documents screen, captured live 2026-08-05: "YOUR DOCUMENTS" above
# "Your wallet is empty" / "Add your first document to get started".
_DOCUMENTS_EMPTY = (AppiumBy.XPATH, '//*[@text="Your wallet is empty"]')

# TODO (Phase B1): capture the per-document card locator — it needs a wallet holding at least
# one credential, and issuance is currently blocked before anything is stored (see
# flows/credential_flow.py: tapping "Add" opens fingerprint enrollment on a device with no
# biometric enrolled). Until then a non-empty documents screen is reported as unavailable
# rather than guessed at.


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of documents in the wallet — only when it is provably empty.

        Counting means switching to the Wallet tab and back, since the dashboard doesn't list
        documents. An empty wallet says so in as many words ("Your wallet is empty"), so that
        case is a real 0. A *populated* list can't be counted yet — the per-document card
        locator needs a wallet with a credential in it, which issuance can't produce on this
        device (see `flows/credential_flow.py`) — so it reports the count as unavailable rather
        than guessing.
        """
        try:
            self.click(_WALLET_TAB)
            self.find(_DOCUMENTS_ROOT)
        except Exception as e:
            raise CredentialCountUnavailable(
                f"authbound: could not open the documents screen: {e}"
            ) from e

        try:
            if wait_present(self.driver, _DOCUMENTS_EMPTY, timeout=2):
                return 0
            raise CredentialCountUnavailable(
                "authbound: the documents screen holds at least one document, but the "
                "per-document card locator has not been captured yet, so the wallet cannot "
                "report how many"
            )
        finally:
            # Back to the dashboard the caller started on.
            try:
                self.click(_HOME_TAB)
            except Exception as e:
                logger.warning(f"[count] authbound: could not return to the Home tab: {e}")
