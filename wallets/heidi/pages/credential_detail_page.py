"""Heidi's credential detail screen and its delete.

Captured live 2026-09-03 (emulator):

    Dashboard -> "Credentials" tile -> credential list -> the card ->
    detail (INFO / METADATA tabs) -> the header's right-hand button -> "DELETE".

**Heidi is the hardest wallet here to locate anything in.** It is Jetpack Compose with no test
tags: the only resource-id anywhere on the screen is `ch.ubique.heidi.android:id/composeContent`,
and not one of the three Buttons carries text or a content-desc. So the anchors below are text
(for screens and dialog actions) and geometry (for the header button), and nothing else was
available.

Two things worth knowing before working on this screen:

- **Screenshots fail here.** The credential screens set Android's `FLAG_SECURE`, so Appium raises
  `ScreenshotException: Does the current view have 'secure' flag set?`. `page_source` still works,
  so capture the tree, not the picture.
- The header's right-hand button deletes **immediately**, opening the confirmation. There is no
  menu in between, unlike unime and procivis.
"""
import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

# The detail screen's tabs. "METADATA" is the anchor: verified absent from both the dashboard and
# the credential list, so it cannot be confused with either.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="METADATA"]')

_HEADER_BUTTONS = (AppiumBy.XPATH, '//android.widget.Button')

# Confirmation: "Delete Credential" / "Do you really want to delete the credential?" /
# CANCEL + DELETE. The actions are upper-case and are the only text anchors on the dialog.
_CONFIRM = (AppiumBy.XPATH, '//*[@text="DELETE"]')
_CANCEL = (AppiumBy.XPATH, '//*[@text="CANCEL"]')

# Header buttons sit at the top of the screen; the floating action button is far below it.
_HEADER_MAX_Y = 300


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("heidi credential detail screen did not load within timeout")

    def _delete_button(self):
        """The header's right-hand button, which is the delete.

        Chosen by geometry because heidi gives it nothing else: the header holds exactly two
        unlabelled Buttons — back on the left, delete on the right — and a third Button (the
        floating action button) sits near the bottom, which the `y` bound excludes. Picking the
        greatest `x` rather than a fixed index keeps this independent of screen size and of the
        order Compose happens to emit them in.
        """
        header = [b for b in self.driver.find_elements(*_HEADER_BUTTONS)
                  if b.rect["y"] < _HEADER_MAX_Y]
        if not header:
            return None
        return max(header, key=lambda b: b.rect["x"])

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this credential's detail screen offers the header delete button."""
        if not on_screen(self.driver, timeout=timeout):
            return False
        return self._delete_button() is not None

    def delete(self):
        """Tap the header delete and confirm. No authentication."""
        button = self._delete_button()
        if button is None:
            raise RuntimeError("heidi credential detail screen has no header delete button")
        button.click()
        self.click(_CONFIRM)

    def cancel_delete(self):
        self.click(_CANCEL)

    def close(self):
        self.driver.back()
