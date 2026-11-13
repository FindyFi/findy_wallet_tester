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

Note where it lands: archiving returns to the **Cards list**, not home, so `archive()` ends
there. The cleanup flow's `delete()` walks back to home afterwards, because the shared prune loop
re-reads the count from the home screen between deletions.

**A toast can sit on the header icon.** After each archive paradym shows "Credential not found";
on the phone it goes in a few seconds, on the AVD it stayed over 50 s (nightly 2026-11-05). It is
clickable and spans the full width, so it covers the archive icon and swallows the tap.
`archive()` taps the toast's Close first and names the toast if the sheet still does not open.
"""
import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

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

# The toast's Close button, scoped to paradym's toaster region ("Notifications alt+T"), which the
# toast sits in. Unscoped, it would also match the Close on the verification "Review the request"
# screen. If paradym renames the region the probe finds nothing and archive() behaves as before.
_TOAST_CLOSE = (AppiumBy.XPATH, '//*[@content-desc="Notifications alt+T"]'
                                '//android.widget.Button[@content-desc="Close"]')
_TOAST_PROBE_S = 1   # the toast is usually absent, so looking for it must be cheap
_TOAST_TAP_S = 2
_TOAST_GONE_S = 3
_TOAST_POLL_S = 0.5


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

    def _clear_toast(self) -> bool:
        """Tap the toast's Close if a toast is up; return whether it is still up afterwards."""
        if not wait_present(self.driver, _TOAST_CLOSE, timeout=_TOAST_PROBE_S):
            return False
        try:
            self.click(_TOAST_CLOSE, timeout=_TOAST_TAP_S)
        except Exception as e:
            # The toast can time out between the probe and the tap; the archive still goes ahead.
            logger.info(f"[page] paradym toast's Close could not be tapped: {e}")
        deadline = time.monotonic() + _TOAST_GONE_S
        while wait_present(self.driver, _TOAST_CLOSE, timeout=0):
            if time.monotonic() >= deadline:
                return True
            time.sleep(_TOAST_POLL_S)
        return False

    def archive(self):
        """Archive (delete) the open card; ends on the Cards list.

        No authentication. Clears a toast off the header icon first. If the confirmation sheet
        does not open, raises at once, naming the toast when one is still up.
        """
        if self._clear_toast():
            logger.info(f"[page] paradym's toast was still up after {_TOAST_GONE_S}s; "
                        "tapping the archive icon anyway")
        self.click(_ARCHIVE)
        if not wait_present(self.driver, _CONFIRM, timeout=self._get_timeout("default")):
            if wait_present(self.driver, _TOAST_CLOSE, timeout=0):
                raise RuntimeError("paradym's toast is still covering the header's archive icon; "
                                   "the 'Archive card?' sheet never opened")
            raise RuntimeError("the 'Archive card?' sheet never opened after tapping the "
                               "header's archive icon")
        self.click(_CONFIRM)

    def cancel(self):
        self.click(_CANCEL)
