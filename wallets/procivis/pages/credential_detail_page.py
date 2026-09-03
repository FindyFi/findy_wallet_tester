"""Procivis's credential detail screen, its action menu, and the delete prompt.

Captured live 2026-09-03:

    Wallet -> card's ".card.header.openDetail" -> CredentialDetailScreen ->
    "CredentialDetailScreen.header.action" (kebab) -> "Delete credential" ->
    CredentialDeletePromptScreen -> **hold** the main button for 3 s -> back on the wallet.

Procivis is the easiest wallet here to locate things in: React Native testIDs are exposed as
resource-ids on nearly everything, and they are stable and semantic. The two exceptions are called
out below, and both are matched on text because they have no id at all.
"""
import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="CredentialDetailScreen"]')

_ACTION_MENU = (AppiumBy.XPATH, '//*[@resource-id="CredentialDetailScreen.header.action"]')

# The action sheet's items carry **no testIDs** — unlike everything else on this screen — so they
# are matched on text. The sheet offers "More information", "Check status update", "Delete
# credential" (in red) and "Close".
_DELETE_ITEM = (AppiumBy.XPATH, '//*[@text="Delete credential"]')

# The confirmation is its own screen, not a dialog.
DELETE_PROMPT = (AppiumBy.XPATH, '//*[@resource-id="CredentialDeletePromptScreen"]')

# **This button must be held, not tapped.** The prompt reads "Yes, delete credential / Hold for 3
# seconds", and a normal click does nothing at all — the screen simply stays up, which looks
# exactly like a locator that missed. Held for a margin over the stated 3 s.
_DELETE_HOLD = (AppiumBy.XPATH, '//*[@resource-id="CredentialDeletePromptScreen.mainButton"]')
_HOLD_MS = 4000

_PROMPT_CLOSE = (AppiumBy.XPATH, '//*[@resource-id="CredentialDeletePromptScreen.header.close"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("procivis credential detail screen did not load within timeout")

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this credential offers the header action that holds the delete."""
        return wait_present(self.driver, _ACTION_MENU, timeout=timeout)

    def delete(self):
        """Open the action menu, choose delete, and hold the confirmation button.

        No authentication. The hold is the whole trick — see `_DELETE_HOLD`.
        """
        self.click(_ACTION_MENU)
        self.click(_DELETE_ITEM)
        if not wait_present(self.driver, DELETE_PROMPT,
                            timeout=self._get_timeout("default")):
            raise RuntimeError(
                "procivis did not show CredentialDeletePromptScreen after 'Delete credential'"
            )
        button = self.find(_DELETE_HOLD)
        self.driver.execute_script(
            "mobile: longClickGesture", {"elementId": button.id, "duration": _HOLD_MS}
        )

    def close(self):
        """Back out to the wallet screen."""
        self.driver.back()
