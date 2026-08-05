import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

# TODO: replace with the locator that uniquely identifies the home screen
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TODO: home screen heading"]')

# TODO: pick whichever of these the wallet actually offers and delete the other.
# A count *label* is preferable when the wallet has one — it reports the wallet's own total
# rather than however many cards happen to be rendered.
_credential_count = (AppiumBy.XPATH, '//*[contains(@text, "TODO: credential count label")]')
_credential_card = (AppiumBy.XPATH, '//*[@text="TODO: one element per credential"]')


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credentials currently in the wallet.

        How you count is up to the wallet — parse a label, count cards, count occurrences in
        the page source, navigate to a separate list screen first. What every wallet must do
        (see `base/credential_count.py`) is:

        1. return a real number or raise `CredentialCountUnavailable` — **never** a silent 0,
           or "my locator broke" becomes indistinguishable from "the wallet is empty";
        2. verify the screen first, so a zero means "I looked" and not "I couldn't look";
        3. leave the app on the screen it was called on (see heidi for a wallet that has to
           navigate to a list screen and back).

        TODO: implement once the home screen layout is known; delete the branch you don't use.
        """
        if not wait_present(self.driver, SCREEN_ID, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "example: home screen is not showing, so a count would mean 'could not look', "
                "not 'wallet is empty'"
            )

        # Option A — the wallet prints a count ("3 credentials", "3 cards total"):
        try:
            text = self.find(_credential_count).get_attribute("text") or ""
        except Exception as e:
            raise CredentialCountUnavailable(f"example: count label not found: {e}") from e
        match = re.search(r"\d+", text)
        if not match:
            # TODO: if an empty wallet renders something like "No credentials", return 0 for
            # those texts explicitly (see heidi) instead of treating them as unreadable.
            raise CredentialCountUnavailable(
                f"example: count label read {text!r}, which contains no number"
            )
        return int(match.group())

        # Option B — one element per credential (see gataca, hovi, unime):
        # try:
        #     return len(self.driver.find_elements(*_credential_card))
        # except Exception as e:
        #     raise CredentialCountUnavailable(f"example: card lookup failed: {e}") from e
