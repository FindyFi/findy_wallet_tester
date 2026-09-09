from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.cleanup import DeleteRefused  # noqa: F401  (re-exported for this wallet's flow)
# Defined in base/cleanup.py so the shared prune loop can catch it. It used to be declared
# here, which meant the loop caught a different class of the same name and hovi's
# restart-and-retry never ran.
from base.utils import wait_present

# hovi's expanded credential view and its delete path.
#
# Tapping a card on the home screen does NOT navigate. hovi expands it in place: the "Credentials"
# heading is replaced by "Credential Details" and the claims, and two controls appear, `Done` and an
# unlabelled delete icon.
#
# Nothing here carries a resource-id (React Native), so clickable controls are matched by
# content-desc and headings by text.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Credential Details"]')

# The delete control has no label, only this private-use glyph from hovi's icon font. If an update
# changes the font this stops matching and `can_delete()` reports False rather than tapping the
# wrong control; re-capture it from the row it shares with `Done`.
#
# That is not hypothetical: build 34 moved it from U+EEF7 to U+F5F6, and because a missing glyph
# reads as "this credential offers no delete", the prune loop logged "Credential cannot be deleted.
# Stopping prune" and left the wallet full — a broken locator published as a wallet property.
# Re-captured on the device 2026-09-09 and confirmed by tapping it: it opens "Delete Credential?".
_DELETE_ICON_GLYPH = "\uf5f6"
_DELETE = (AppiumBy.XPATH, f'//*[@content-desc="{_DELETE_ICON_GLYPH}"]')

_DONE = (AppiumBy.XPATH, '//*[@content-desc="Done"]')

# Confirmation dialog: "Delete Credential?" with Cancel and Delete. This dialog is the only gate.
# No authentication is involved anywhere in the flow.
CONFIRM_ID = (AppiumBy.XPATH, '//*[@text="Delete Credential?"]')
_CONFIRM_DELETE = (AppiumBy.XPATH, '//*[@content-desc="Delete"]')

# hovi can refuse the deletion after the confirmation is accepted, reporting "Failed to delete
# credential" and staying on the expanded card. Without this check it surfaces as `Hovi home screen
# did not load within timeout` from whatever the caller does next.
_DELETE_FAILED = (AppiumBy.XPATH,
                  '//*[@content-desc="Failed to delete credential"]'
                  ' | //*[@text="Failed to delete credential"]')
_CANCEL_DELETE = (AppiumBy.XPATH, '//*[@content-desc="Cancel"]')




def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Hovi credential details did not open within timeout")

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this credential offers the delete icon."""
        return wait_present(self.driver, _DELETE, timeout=timeout)

    def delete(self):
        """Tap the delete icon and confirm on the "Delete Credential?" dialog.

        The dialog is waited for rather than assumed: tapping Delete blind would, if the icon ever
        stops opening a dialog, hit whatever else occupies that position. And confirming is not the
        end of it. hovi can still refuse, so the failure banner is checked before returning.
        """
        self.click(_DELETE)
        if not wait_present(self.driver, CONFIRM_ID, timeout=self._get_timeout("default")):
            raise RuntimeError(
                "Hovi delete icon did not open the 'Delete Credential?' confirmation"
            )
        self.click(_CONFIRM_DELETE)

        if wait_present(self.driver, _DELETE_FAILED, timeout=3):
            raise DeleteRefused(
                "Hovi refused to delete the credential. It reported \"Failed to delete "
                "credential\" and stayed on the expanded card. The delete path itself works "
                "(the confirmation was accepted); the wallet rejected the removal."
            )

    def cancel_delete(self):
        """Dismiss the confirmation, leaving the credential in place."""
        self.click(_CANCEL_DELETE)

    def close(self):
        """Leave the expanded-card mode via Done."""
        self.click(_DONE)
