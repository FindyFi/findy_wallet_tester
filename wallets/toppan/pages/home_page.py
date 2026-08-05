from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TOPPAN Wallet"]')

# One card View per credential, inside the scrollable list. Toppan is a WebView app, and
# Chrome prunes the *descendants* of cards below the scroll viewport while keeping the card
# node itself — so counting anything inside a card under-reports a long list. Measured on
# 2026-08-05: a wallet showing 13+ cards had 14 card containers in the tree but only 11 still
# carried their "Issued on" line, which is what the old count read. That pins the number and
# makes every issuance look like it stored nothing.
# The [*] predicate skips the empty placeholder View the list keeps after the last card.
_credential_card = (AppiumBy.XPATH,
    '//android.view.View[@scrollable="true"]/android.view.View/android.view.View[*]')



class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards on the home screen.

        Counts card containers, not text inside them — see `_credential_card` for why that
        distinction is what makes the number move at all. Cards are on home, so no navigation,
        but home has to be showing or zero cards would mean "could not look" rather than
        "wallet is empty".

        Still bounded by what Chrome puts in the accessibility tree: a list long enough that
        whole cards fall outside it will read low. Keeping toppan's wallet trimmed (it has a
        working reset) is what keeps this honest.
        """
        if not wait_present(self.driver, SCREEN_ID, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "toppan: home screen ('TOPPAN Wallet') is not showing, so a count would mean "
                "'could not look', not 'wallet is empty'"
            )
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception as e:
            raise CredentialCountUnavailable(f"toppan: credential card lookup failed: {e}") from e
