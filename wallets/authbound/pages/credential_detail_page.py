from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

# Captured live on 2026-08-17 by opening a credential from the dashboard carousel. The screen
# shows the credential name as its title, "1/1 instances remaining", a "DOCUMENT DETAILS" section
# of raw claims (exp, iat, jti, nbf, sub, plus expandable Pension/Person groups), an "ISSUER"
# section, and a "Delete document" button pinned at the bottom.
#
# There is no `*_screen_root` id here — the only ids on the screen are the delete button and the
# confirm sheet's two buttons. "DOCUMENT DETAILS" is the stable screen anchor: verified absent
# from both the dashboard and the documents list, so it cannot be confused with either.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="DOCUMENT DETAILS"]')

# These ids are package-prefixed, so AppiumBy.ID resolves them — unlike the bare Compose
# testTags on the bottom navigation, which need XPath (see home_page.py for that trap).
_DELETE = (AppiumBy.ID, "io.authbound.wallet:id/document_details_screen_delete_button")

# Confirmation bottom sheet: "Delete document?" / "This document will be permanently deleted from
# your wallet and may affect access to services." / Cancel + Delete.
_CONFIRM_DELETE = (
    AppiumBy.ID,
    "io.authbound.wallet:id/document_details_screen_dialogue_delete_document_positive_button")
_CANCEL_DELETE = (
    AppiumBy.ID,
    "io.authbound.wallet:id/document_details_screen_dialogue_delete_document_negative_button")

_BACK = (AppiumBy.XPATH, '//*[@content-desc="Go Back"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("authbound document details screen did not load within timeout")

    def can_delete(self, timeout: float = 2) -> bool:
        """True if this document offers a delete button at all."""
        return wait_present(self.driver, _DELETE, timeout=timeout)

    def delete(self):
        """Tap "Delete document" and confirm on the bottom sheet.

        Unlike gataca, authbound requires **no authentication** to delete: confirming returns
        straight to the dashboard with the credential gone (verified live 2026-08-17, wallet went
        4 → 3). Callers must therefore not wait for a biometric or PIN prompt — waiting for one
        would simply time out.
        """
        self.click(_DELETE)
        self.click(_CONFIRM_DELETE)

    def cancel_delete(self):
        """Dismiss the confirm sheet, leaving the document in place."""
        self.click(_CANCEL_DELETE)

    def close(self):
        """Back out to the screen this was opened from."""
        self.click(_BACK)
