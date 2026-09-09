import re
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Hello!" and @heading="true"]')


# Home's shortcut into the full card list, labelled "All cards. N cards total".
_ALL_CARDS = (AppiumBy.XPATH, '//*[contains(@content-desc, "All cards")]')

# How long to let the Cards list settle after it appears, before tapping a row.
#
# `wait_present` returns the moment an arrow exists in the tree, which is *during* the list's entry
# transition, and a tap that lands then is silently dropped — the app just stays on the list.
# Measured 2026-09-03: without this, the open after every delete failed on the first attempt, all
# eight iterations of a prune, deterministically. The same sequence with a 2 s pause opened the
# card first time. Two wrong guesses preceded this one (an animation race that a retry would fix,
# and the archive toast swallowing the tap) — neither survived the probe, this did.
_LIST_SETTLE = 2.0

# A row's arrow on the Cards list. Rows carry no content-desc of their own and the row container
# is not clickable — the arrow is. Matched structurally: a card row is the only ViewGroup holding
# both a TextView (the card name) and a Button (the arrow). The search field's container has an
# EditText instead, so it does not match. Verified against the captured list: 7 arrows for the 7
# rows in the tree, no false positives.
_CARD_ROW_ARROW = (AppiumBy.XPATH,
    '//android.view.ViewGroup[android.widget.TextView and android.widget.Button]'
    '/android.widget.Button')


class HomePage(BasePage):
    _heading = SCREEN_ID
    _cards_total = (AppiumBy.XPATH, '//*[contains(@text, "card") and contains(@text, "total")]')

    def open_credential(self) -> bool:
        """Open the first card's details, via the Cards list. False when the wallet shows none.

        Two navigation steps rather than one: paradym keeps no card list on home, only a count and
        a shortcut. Archiving returns to the Cards list, so the next iteration walks home and comes
        back — slower than the wallets that list cards on home, but it keeps every iteration
        starting from the same known screen.
        """
        self.click(_ALL_CARDS)
        if not wait_present(self.driver, _CARD_ROW_ARROW, timeout=self._get_timeout("default")):
            return False
        time.sleep(_LIST_SETTLE)
        self.click(_CARD_ROW_ARROW)
        return True

    def return_to_home(self, tries: int = 6) -> bool:
        """Walk back until the home heading shows. Used after an archive, which lands on the list.

        Bounded, because a screen that never yields to back would otherwise loop forever — and
        pressing back past home would leave the app entirely.
        """
        for _ in range(tries):
            if wait_present(self.driver, self._heading, timeout=2):
                return True
            self.driver.back()
        return wait_present(self.driver, self._heading, timeout=2)

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
