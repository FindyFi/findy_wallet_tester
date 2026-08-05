import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Hello!" and @heading="true"]')


class HomePage(BasePage):
    _heading = SCREEN_ID
    _cards_total = (AppiumBy.XPATH, '//*[contains(@text, "card") and contains(@text, "total")]')

    def wait_until_loaded(self, timeout=10):
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(self._heading)
            )
        except TimeoutException:
            raise Exception("Home screen did not load: 'Hello!' heading not found")

    def count_credentials(self) -> int:
        """Return the number of credentials stored in the wallet.

        Reads the "N cards total" label on the home screen — no navigation, and no counting of
        cards, so the number is the wallet's own and not limited to what fits on screen.
        Paradym omits the label entirely when the wallet is empty, so a missing label is a
        legitimate 0 here (unlike heidi, which renders "No credentials"). That is exactly why
        the screen check comes first: off the home screen the label is *also* missing.
        """
        if not wait_present(self.driver, self._heading, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "paradym: home screen ('Hello!') is not showing, so a missing card-count label "
                "would mean 'could not look', not 'wallet is empty'"
            )
        try:
            text = self.driver.find_element(*self._cards_total).text or ""
        except Exception:
            return 0

        match = re.search(r"\d+", text)
        if not match:
            raise CredentialCountUnavailable(
                f"paradym: card-count label read {text!r}, which contains no number"
            )
        return int(match.group())
