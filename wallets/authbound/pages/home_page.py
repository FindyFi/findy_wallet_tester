import logging
import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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

# The wallet reports its own total next to the "Wallet" header title — a sibling TextView
# reading "· 3" (U+00B7, space, digits). Captured live 2026-08-05 with one document present;
# an empty wallet omits the label entirely. Preferred over counting cards: it is the wallet's
# own number, so it can't be truncated by what happens to be rendered.
_DOCUMENT_COUNT = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Wallet"]'
    '/following-sibling::android.widget.TextView[1]'
)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of documents in the wallet.

        Counting means switching to the Wallet tab and back, since the dashboard doesn't list
        documents — authbound is the second wallet after heidi where counting is a navigation
        step. The number comes from the wallet's own header total ("Wallet · 3") rather than
        from counting cards, so it is not limited to what happens to be rendered.

        An empty wallet omits that label and says "Your wallet is empty" instead, which is a
        real 0. A missing label with no empty-state text means something changed and is
        reported as unavailable rather than guessed at.
        """
        try:
            self.click(_WALLET_TAB)
            self.find(_DOCUMENTS_ROOT)
        except Exception as e:
            raise CredentialCountUnavailable(
                f"authbound: could not open the documents screen: {e}"
            ) from e

        try:
            try:
                label = self.find(_DOCUMENT_COUNT, timeout=2).get_attribute("text") or ""
            except Exception:
                if wait_present(self.driver, _DOCUMENTS_EMPTY, timeout=2):
                    return 0
                raise CredentialCountUnavailable(
                    "authbound: no document-count label next to the 'Wallet' title and no "
                    "empty-state text either — the documents screen layout has changed"
                )

            match = re.search(r"\d+", label)
            if not match:
                raise CredentialCountUnavailable(
                    f"authbound: document-count label read {label!r}, which contains no number"
                )
            return int(match.group())
        finally:
            # Back to the dashboard the caller started on.
            try:
                self.click(_HOME_TAB)
            except Exception as e:
                logger.warning(f"[count] authbound: could not return to the Home tab: {e}")
