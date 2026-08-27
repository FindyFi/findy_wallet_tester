from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

# Home screen is identified by the "Scan QR" bottom nav button which is always present.
# The heading alternates between "This is your wallet" (empty) and "Credentials" (has creds).
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Scan QR" or @text="This is your wallet" or @text="Credentials"]')

# Credential cards are ViewGroup elements with a non-empty content-desc on the home screen.
# Each card has content-desc like ", EläKeläIstodiste, Issuer, Findynet".
_credential_card = (AppiumBy.XPATH,
    '//android.view.ViewGroup[contains(@content-desc,"Issuer")]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Hovi home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards on the home screen.

        No navigation — the cards are on home. The screen check matters because hovi's home
        heading differs between the empty and non-empty states, so "no cards found" is only
        meaningful once we know home is up.
        """
        if not on_screen(self.driver, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "hovi: home screen is not showing, so a count would mean 'could not look', "
                "not 'wallet is empty'"
            )
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception as e:
            raise CredentialCountUnavailable(f"hovi: credential card lookup failed: {e}") from e

    def open_credential(self) -> bool:
        """Open the first credential card on the home screen.

        Returns False when no card is present after waiting, so a caller can stop looping. Tapping
        a card does not navigate; hovi expands it in place (see pages/credential_detail_page.py).

        Waits rather than sampling, and taps through `BasePage.click` which re-locates on a stale
        reference: deleting a card re-renders the list underneath us.
        """
        if not wait_present(self.driver, _credential_card,
                            timeout=self._get_timeout("default")):
            return False
        self.click(_credential_card)
        return True
