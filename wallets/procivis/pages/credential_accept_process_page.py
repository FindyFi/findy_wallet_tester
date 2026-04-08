import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present
from wallets.procivis.pages.invitation_error_details_page import InvitationErrorDetailsPage

logger = logging.getLogger(__name__)

# InvitationProcessScreen — shown when the deeplink/invitation itself fails (e.g. waltid)
# CredentialAcceptProcessScreen — shown during/after accepting the credential offer (e.g. sphereon)
INVITATION_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="InvitationProcessScreen"]')
ACCEPT_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="CredentialAcceptProcessScreen"]')
SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="InvitationProcessScreen" or @resource-id="CredentialAcceptProcessScreen"]')

# Generic locators that match either process screen prefix.
# InvitationProcessScreen signals failure via .animation.error;
# CredentialAcceptProcessScreen shows an "Issuance failed" label instead.
_error = (AppiumBy.XPATH, '//*[contains(@resource-id, ".animation.error") or @text="Issuance failed"]')
_info = (AppiumBy.XPATH, '//*[contains(@resource-id, ".header.info")]')
_show_more = (AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen.cause.expandValueButton"]')

class CredentialAcceptProcessPage(BasePage):
    def wait_for_result(self):
        """Wait for credential issuance to complete.

        Success path: screen disappears automatically (navigates to credential detail).
        Error path: error animation visible — taps info button to open InvitationErrorDetailsScreen,
                    collects Code/Message/Cause fields, logs them, then raises RuntimeError.
        """
        t = self._get_timeout("credential_offer")
        self.find(SCREEN_ID, timeout=t)

        try:
            WebDriverWait(self.driver, t).until(
                EC.any_of(
                    EC.invisibility_of_element_located(SCREEN_ID),
                    EC.presence_of_element_located(_error),
                )
            )
        except TimeoutException:
            raise RuntimeError("Credential issuance did not complete within timeout")

        if not wait_present(self.driver, _error, timeout=1):
            return  # success: screen disappeared

        # Tap info to open error details and collect them for the report
        details: dict = {}
        try:
            self.click(_info)
            self.click(_show_more)
            details = InvitationErrorDetailsPage(self.driver, **self._page_kwargs()).collect_details()
        except Exception:
            pass
        # Return to InvitationProcessScreen before raising so teardown can close it cleanly
        try:
            self.driver.back()
        except Exception:
            pass

        detail_str = ", ".join(f"{k}: {v}" for k, v in details.items()) if details else "no details available"
        logger.error(f"[credential_accept] Issuance failed — {detail_str}")
        raise RuntimeError(f"Credential issuance failed — {detail_str}")

    def _page_kwargs(self) -> dict:
        return {"timeouts": self.timeouts, "debug": self.debug}
