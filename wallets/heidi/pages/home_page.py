import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

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


class HomePage(BasePage):
    def open_settings(self):
        self.click(_SETTINGS_BTN)

    def count_credentials(self) -> int:
        """Return the number of credentials in the wallet.

        Heidi's home is a dashboard — credentials live on a separate list screen
        reached via the "Credentials" tile. That screen shows a subtitle below
        the "Credentials" title reading "No credentials" when empty or
        "<n> credentials" otherwise, so we open the list, parse the count, then
        return to the dashboard so the caller resumes from a known screen.
        """
        try:
            self.click(_CREDENTIALS_TILE)
            label = self.find(_LIST_COUNT_LABEL).get_attribute("text") or ""
        except Exception:
            label = ""
        finally:
            try:
                self.driver.back()
            except Exception:
                pass
        match = re.search(r"\d+", label)
        return int(match.group()) if match else 0

    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")
