"""UniMe's "Credential Information" screen — claims, and the menu that deletes.

Captured live 2026-09-03. Reached by tapping a credential card on home; no authentication is
required, unlike toppan, which gates the same screen behind a device-auth prompt.

    Home -> card -> Credential Information -> "Open credential menu" (kebab, top right) ->
    "Delete credential" -> confirm dialog -> "Delete" -> back on Home.

The menu also offers "Share to LinkedIn" and "Edit display name", neither of which anything here
uses.
"""
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Credential Information"]')

# The kebab. Matched on `content-desc` because its `resource-id` is a generated React Native id
# ("K6G5r1PDxl" in the capture) — a value that will differ on the next build, and exactly the kind
# of locator that fails silently later.
_MENU = (AppiumBy.XPATH, '//*[@content-desc="Open credential menu"]')

# The menu item. **`@clickable="true"` is load-bearing, not decoration**: the confirmation dialog
# that this item opens uses "Delete credential" as its *heading* too, so the bare text matches two
# different things one tap apart. The heading is not clickable; the menu item is.
_DELETE_ITEM = (AppiumBy.XPATH, '//*[@text="Delete credential" and @clickable="true"]')

# Confirmation dialog: "Delete credential" / "Are you sure you want to delete this credential from
# your wallet? This action cannot be undone." / Delete + Cancel.
_CONFIRM = (AppiumBy.XPATH, '//*[@text="Delete"]')
_CANCEL = (AppiumBy.XPATH, '//*[@text="Cancel"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("unime credential information screen did not load within timeout")

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this credential offers the menu that holds the delete action.

        Only the menu's presence is checked, not the item inside it — opening the menu to look
        would be a side effect in what callers treat as a question.
        """
        return wait_present(self.driver, _MENU, timeout=timeout)

    def delete(self):
        """Open the menu, choose "Delete credential", and confirm. No authentication."""
        self.click(_MENU)
        self.click(_DELETE_ITEM)
        self.click(_CONFIRM)

    def cancel_delete(self):
        """Dismiss the confirmation, leaving the credential in place."""
        self.click(_CANCEL)

    def close(self):
        """Back out to home. The screen's back arrow carries no text or content-desc, so the
        hardware back is the only locator-free way off this screen."""
        self.driver.back()
