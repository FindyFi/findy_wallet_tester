import logging
import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable

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

# On the credential-list screen the subtitle below the "Credentials" title is the
# count label: "No credentials" when empty, "<n> credentials" otherwise.
_LIST_COUNT_LABEL = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="Credentials"]'
    '/following-sibling::android.widget.TextView[1]'
)

# Count-label texts that mean zero without containing a digit. Anything else without a number
# in it is treated as unreadable rather than as an empty wallet.
_EMPTY_LIST_TEXTS = ("No credentials",)


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

    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")
