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

# Heidi's home is a dashboard with a "Credentials" tile (not a credential list).
# "WALLET" also appears in Settings/About, so we anchor on "Credentials".
SCREEN_ID = (AppiumBy.XPATH, '//*[contains(@text, "Credentials")]')


# The settings button is the rightmost header button (second Button sibling after "WALLET").
_SETTINGS_BTN = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="WALLET"]'
    '/following-sibling::android.widget.Button[last()]'
)

# The dashboard tile that opens the credential list. "Your digital credentials"
# is its subtitle and is unique to this tile.
_CREDENTIALS_TILE = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="Your digital credentials"]]'
)

# The credential list's count label — "<n> DIGITAL CREDENTIALS" (upper case), or "No credentials"
# when empty.
#
# **Matched on the label's own text, not as the sibling of the "Credentials" title.** The sibling
# form was ambiguous across two screens: the dashboard's tile is also a "Credentials" TextView
# followed by a subtitle, so on the dashboard it read "Your digital credentials" — the tile's
# subtitle — instead of a count. Measured 2026-09-03 at the end of a prune, when the tile tap had
# not navigated yet and the read happened on the dashboard. It failed safe (no digits, so the
# count was reported unavailable rather than invented), but it made the count unavailable for a
# wallet that was simply empty.
#
# This form exists on the list screen and nowhere else, so waiting for it *is* the screen check.
_LIST_COUNT_LABEL = (AppiumBy.XPATH,
    '//android.widget.TextView[contains(@text, "DIGITAL CREDENTIALS")'
    ' or @text="No credentials"]'
)

# Count-label texts that mean zero without containing a digit. Anything else without a number
# in it is treated as unreadable rather than as an empty wallet.
_EMPTY_LIST_TEXTS = ("No credentials",)


# A credential card on the list screen. Heidi has no test tags, so this is structural: the card is
# the only clickable View there that carries a TextView (the credential's name). The list's other
# clickable View is a header button with no text at all.
_CREDENTIAL_CARD = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" and .//android.widget.TextView]'
)


class HomePage(BasePage):
    def open_settings(self):
        self.click(_SETTINGS_BTN)

    def count_credentials(self) -> int:
        """Return the number of credentials in the wallet.

        Heidi's home is a dashboard, so this is the only wallet where counting is a navigation
        step: open the "Credentials" tile, read the subtitle below the "Credentials" title
        ("No credentials" when empty, "<n> credentials" otherwise), then go back so the caller
        resumes on the dashboard it started from.

        The label itself is the proof we reached the list screen — no separate screen check is
        needed, and a missing label means the navigation failed rather than the wallet being
        empty.
        """
        try:
            self.click(_CREDENTIALS_TILE)
        except Exception as e:
            raise CredentialCountUnavailable(
                f"heidi: could not open the credential list from the dashboard tile: {e}"
            ) from e

        try:
            label = self.find(_LIST_COUNT_LABEL).get_attribute("text") or ""
        except Exception as e:
            raise CredentialCountUnavailable(
                f"heidi: credential-list count label not found: {e}"
            ) from e
        finally:
            # Always return to the dashboard, including when the read above failed.
            try:
                self.driver.back()
            except Exception as e:
                logger.warning(f"[count] heidi: could not return to the dashboard: {e}")

        if label.strip() in _EMPTY_LIST_TEXTS:
            return 0

        match = re.search(r"\d+", label)
        if not match:
            raise CredentialCountUnavailable(
                f"heidi: count label read {label!r}, which contains no number and is not a "
                f"known empty-state text {list(_EMPTY_LIST_TEXTS)}"
            )
        return int(match.group())

    def open_credential(self) -> bool:
        """Open the first credential's detail screen, via the credential list.

        Must start on the dashboard, since the tile is what navigates. Returns False when the list
        presents no card, which is how an empty wallet ends a prune.
        """
        try:
            self.click(_CREDENTIALS_TILE)
        except Exception as e:
            logger.warning(f"[cleanup] heidi: could not open the credential list: {e}")
            return False
        if not wait_present(self.driver, _CREDENTIAL_CARD,
                            timeout=self._get_timeout("default")):
            return False
        self.click(_CREDENTIAL_CARD)
        return True

    def return_to_dashboard(self, tries: int = 4) -> bool:
        """Walk back until the dashboard tile is showing.

        Needed after a delete, which lands on the credential list — and `count_credentials()`
        navigates *from* the dashboard, so an iteration that ended on the list would stall the
        prune. Bounded, because pressing back past the dashboard leaves the app.

        `SCREEN_ID` cannot serve here: it matches `contains(@text, "Credentials")`, which is true
        on the list screen too. The tile is what actually distinguishes the dashboard.
        """
        for _ in range(tries):
            if wait_present(self.driver, _CREDENTIALS_TILE, timeout=2):
                return True
            self.driver.back()
        return wait_present(self.driver, _CREDENTIALS_TILE, timeout=2)

    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")
