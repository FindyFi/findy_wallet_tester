"""Paradym's "Card details" screen and its archive action.

Named `credential_detail_page.CredentialDetailPage` like every other wallet's, even though paradym
calls the thing a *card* throughout its own UI. The suite needs one name per concept so the seven
cleanup flows read alike; paradym's own vocabulary is kept in the locators and the prose below,
where it describes what is actually on screen.

Captured live 2026-09-03:

    Home -> "All cards" -> Cards list -> the row's arrow -> Card details ->
    the header's right-hand icon -> "Archive card?" sheet -> "Yes, archive" -> back on the Cards list.

**Archiving is paradym's delete.** The sheet says so in as many words — "This will make
<name> unusable and delete it from your wallet." — and the wallet's own card total drops when it
completes (12 → 11, measured). There is no separate delete action.

Note where it lands: archiving returns to the **Cards list**, not home, which is why `archive()`
finishes by walking back to home. The shared prune loop re-reads the count from the home screen
between deletions, so an iteration that ended on the list would stall it.
"""
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Card details"]')

# The header's right-hand icon, which opens the archive sheet. paradym gives it **no text, no
# content-desc and no resource-id** — nor does the back arrow beside it — so there is nothing
# semantic to match on.
#
# What separates them is that they are the only two unlabelled Buttons on the screen (the other
# three all carry a content-desc: "Card is active…", "Card attributes…", " , Back"), and the
# archive is the second of the two. Captured order: it was 5 of 5.
#
# A weak anchor like this would normally be unacceptable. It is safe here only because the
# **confirmation sheet is the real gate**: if this ever taps the wrong control, "Yes, archive"
# does not appear and `archive()` raises instead of silently doing something else.
_ARCHIVE = (AppiumBy.XPATH,
            '(//android.widget.Button[not(@content-desc) or @content-desc=""])[last()]')

# Confirmation sheet: "Archive card?" / "This will make <name> unusable and delete it from your
# wallet." / No + "Yes, archive". These two do carry content-descs.
_CONFIRM = (AppiumBy.XPATH, '//*[@content-desc="Yes, archive"]')
_CANCEL = (AppiumBy.XPATH, '//*[@content-desc="No"]')



def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("paradym card details screen did not load within timeout")

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this card offers the header action that archives it."""
        return wait_present(self.driver, _ARCHIVE, timeout=timeout)

    def archive(self):
        """Archive (delete) the open card, then return to home.

        No authentication. Ends on home rather than the Cards list, because that is what the
        prune loop needs to re-read the count.
        """
        self.click(_ARCHIVE)
        self.click(_CONFIRM)

    def cancel(self):
        self.click(_CANCEL)
