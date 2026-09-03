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
#
# These MUST be XPath, not AppiumBy.ID — do not "simplify" them back. The tabs are Compose
# `testTag`s, so they appear in the tree as a BARE resource-id ("dashboard_screen_bottom_
# navigation_item_wallet") with no "<package>:id/" part, unlike every other locator in this
# wallet. Appium's `id` strategy only matches the qualified form (a bare value is looked up as
# `[^:]+:id/<name>$`), so it can never resolve a bare tag: the 2026-08-12 run shows the
# uiautomator2 server answering `no such element` on every retry for 10 s, which BasePage.click
# then reports as the misleading "not clickable after 10s". Verified live 2026-08-13 — the nodes
# are present with clickable=true, enabled=true.
_WALLET_TAB = (AppiumBy.XPATH,
               '//*[@resource-id="dashboard_screen_bottom_navigation_item_wallet"]')
_HOME_TAB = (AppiumBy.XPATH,
             '//*[@resource-id="dashboard_screen_bottom_navigation_item_home"]')
_DOCUMENTS_ROOT = (AppiumBy.ID, "io.authbound.wallet:id/dashboard_documents_screen_root")

# Empty state of the documents screen, captured live 2026-08-05: "YOUR DOCUMENTS" above
# "Your wallet is empty" / "Add your first document to get started".
_DOCUMENTS_EMPTY = (AppiumBy.XPATH, '//*[@text="Your wallet is empty"]')

# The dashboard's credential carousel — one card at a time, with a "Page <n> of <m>" indicator
# under it. Verified live 2026-08-17: the carousel is the ONLY `android.view.View` with
# scrollable="true" on this screen (the outer scroller is an `android.widget.ScrollView`, a
# different class), and it holds exactly one clickable descendant — the front card. Confirmed
# stable at both 4 and 3 credentials.
#
# The cards carry no resource-id and no stable text, so this structural anchor is what there is;
# the documents list on the Wallet tab is worse, having been seen render its cards outside the
# accessibility tree entirely. Tapping the card opens the document details screen.
_CAROUSEL_CARD = (AppiumBy.XPATH,
                  '//android.view.View[@scrollable="true"]//*[@clickable="true"]')

# The wallet reports its own total next to the "Wallet" header title — a sibling TextView
# reading "· 3" (U+00B7, space, digits). Captured live 2026-08-05 with one document present;
# an empty wallet omits the label entirely. Preferred over counting cards: it is the wallet's
# own number, so it can't be truncated by what happens to be rendered.
#
# Re-verified live 2026-08-13 with two credentials (read "· 2"): "Wallet" and "· 2" are adjacent
# sibling TextViews under one parent with nothing between them, so following-sibling[1] is exact.
# The label sits in the app bar, above the screen's Documents/Actions/Health sub-tabs. The cards
# themselves are android.view.View with NO resource-id inside a scrolling list — which is why
# counting them was never a workable alternative.
# A document card in the Wallet tab's list. This is the *complete* enumeration — the dashboard
# carousel is not, which is why both exist (see `open_credential`).
#
# Within the documents root the only clickables are the search field, the search button and the
# cards, so "a clickable View carrying more than one line of text" separates them exactly:
# measured 2026-09-03, the search EditText holds 1 TextView (its hint), the search button 0, and
# the card 6 ("AB", "AUTHBOUND ID", "Issued", "Eläkeläistodiste", "Authbound Digital ID",
# "Valid until: 10 Sept 2026"). Restricting to `android.view.View` also excludes the EditText.
#
# Matching on any of that text would be wrong — it is the credential's own content and changes
# with the issuer.
_DOCUMENTS_CARD = (AppiumBy.XPATH,
    '//*[@resource-id="io.authbound.wallet:id/dashboard_documents_screen_root"]'
    '//android.view.View[@clickable="true" and count(.//android.widget.TextView) > 1]')

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

    def open_credential(self) -> bool:
        """Open the credential currently at the front of the dashboard carousel.

        Returns False only when the dashboard still presents no card after waiting for one, so a
        caller can stop looping. Deleting a credential returns to the dashboard with the next one
        at the front, which makes "open the front card, delete, repeat" a complete traversal — no
        indexing into a list that shifts underneath us.

        Must be called on the Home tab; `count_credentials()` leaves the app there.

        **Waits for the card rather than sampling for it.** Callers arrive straight out of
        `count_credentials()`, which returns from the Wallet tab via the Home tab, and the
        dashboard has not composed yet at that moment. A bare `find_elements` reports "no
        credentials" for a wallet that still holds several — which is what stopped the 2026-08-17
        cleanup three deletions in, with the count still reading 2. The deletions before it only
        worked by luck: each lost its first tap to a stale reference, and that retry's short sleep
        was what gave the dashboard time to appear.

        `BasePage.click` re-locates and retries on staleness, which covers the recomposition that
        follows the tab switch — so the card must not be cached here.

        **The dashboard carousel is not a complete enumeration**, which cost a cleanup on
        2026-09-03: with the wallet at 1 the dashboard was *empty* — no carousel, no card, nothing
        but the app bar and the nav bar — while the Wallet tab still listed the document and its
        detail screen still offered "Delete document". The prune therefore stopped one short and
        reported "no deletable credential left", which was false. The earlier 4 → 3 observation
        that this path was built on had only ever demonstrated a single deletion, so it never
        showed the gap.

        So the carousel is tried first — it is the proven path and it is already on screen — and
        the documents list is the fallback that makes the traversal complete.
        """
        if wait_present(self.driver, _CAROUSEL_CARD, timeout=self._get_timeout("default")):
            self.click(_CAROUSEL_CARD)
            return True
        return self._open_from_documents_list()

    def _open_from_documents_list(self) -> bool:
        """Open the first document from the Wallet tab's list. False when the list is empty.

        Leaves the app on the Wallet tab, which is safe for the prune loop: `dashboard_screen_root`
        is present on both tabs, so `wait_until_loaded()` still passes, and `count_credentials()`
        navigates from wherever it is and returns to the Home tab itself.

        The list needs its own wait. It renders late enough that a fixed sleep of 2 s saw an empty
        accessibility tree — no cards, no search box, not even the documents root — which is the
        same "rendering outside the tree" this wallet is known for. Waiting on the root and then
        on the card is what makes it reliable.
        """
        try:
            self.click(_WALLET_TAB)
            self.find(_DOCUMENTS_ROOT)
        except Exception as e:
            logger.warning(f"[cleanup] authbound: could not open the documents list: {e}")
            return False

        if not wait_present(self.driver, _DOCUMENTS_CARD,
                            timeout=self._get_timeout("default")):
            return False
        self.click(_DOCUMENTS_CARD)
        return True

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
