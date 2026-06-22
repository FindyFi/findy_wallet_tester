from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# "My credentials" heading is always visible on the home screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="My credentials"]')

# Credential cards live only inside the *scrollable* vertical credentials list — an
# android.widget.ScrollView with scrollable="true". Scoping to it is essential: the outer
# (non-scrollable) ScrollViews also contain the header "Add" button, the "JWK Identity" picker,
# and (via a HorizontalScrollView) the "All"/"Favorites" chips, while "Scan" and the bottom-nav
# tabs sit outside the list. A plain //ScrollView match grabbed all of those — which made
# count_credentials over-count and made pruning click "Add" (opening "Create a credential")
# instead of a credential. Matching the exact class android.widget.ScrollView (not
# HorizontalScrollView) keeps the All/Favorites chips out. A card's content-desc may be a bare
# issuer name with no comma (e.g. "Kela"), so we only require it be non-empty.
_credential_card = (AppiumBy.XPATH,
    '//android.widget.ScrollView[@scrollable="true"]'
    '//android.view.ViewGroup[@clickable="true" and string-length(@content-desc) > 0]'
)

# The wallet's own device credential — must never be pruned. Its card content-desc starts with this.
_SELF_ATTESTED_PREFIX = "Self-attested"

# Top-left button on home showing the active DID's alias (e.g. "JWK Identity", "Gataca").
# It's the only Button with a non-empty content-desc that isn't the "Add" action.
_DID_ALIAS_BTN = (AppiumBy.XPATH,
    '//android.widget.Button[@clickable="true" and @content-desc!="" and @content-desc!="Add"]'
)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credentials currently in the wallet."""
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception:
            return 0

    def active_did_alias(self) -> str:
        """Return the active DID's alias shown on the home top-left button (e.g. 'JWK Identity')."""
        try:
            return self.driver.find_element(*_DID_ALIAS_BTN).get_attribute("content-desc") or ""
        except Exception:
            return ""

    def open_deletable_credential(self) -> bool:
        """Open the first credential card that is safe to delete (i.e. not the self-attested
        device credential). Returns True if one was opened, False if none remain."""
        for card in self.driver.find_elements(*_credential_card):
            desc = card.get_attribute("content-desc") or ""
            if not desc.startswith(_SELF_ATTESTED_PREFIX):
                card.click()
                return True
        return False
