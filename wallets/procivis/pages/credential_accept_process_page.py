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
#
# procivis draws a failure with one of two Lottie animations and they are NOT interchangeable:
# `.animation.error` is the hard rejection, `.animation.warning` the transport/connection failure
# ("Connection failed"). Measured across every XML dump this repo has captured: 42 `.warning`
# against 16 `.error`, and CredentialAcceptProcessScreen has only ever shown `.warning` — the
# `@text="Issuance failed"` alternative was the sole reason that screen was ever caught at all.
#
# Matching only `.error` meant the common failure fell through to the timeout at the end of the
# wait below, and the details screen behind `.header.info` — which is on screen, in those very
# dumps — was never opened. Four of five issuance failures in the 2026-09-21 run reported
# "did not complete within timeout" and no Code/Message/Cause because of this.
#
# Both variants are named explicitly rather than matching `contains(@resource-id, ".animation.")`:
# the wait below treats any `_error` hit as terminal, so a success animation caught mid-dismissal
# would turn a passing issuance red.
_error = (
    AppiumBy.XPATH,
    '//*[contains(@resource-id, ".animation.error")'
    ' or contains(@resource-id, ".animation.warning")'
    ' or @text="Issuance failed"]',
)
_info = (AppiumBy.XPATH, '//*[contains(@resource-id, ".header.info")]')
_show_more = (AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen.cause.expandValueButton"]')

# The label that carries the wallet's own words. Read off 75 captured dumps, procivis has exactly
# three: "Connection failed" (.animation.warning, 42), "No credential interaction found."
# (.animation.error, 16) and "Issuance failed" (CredentialAcceptProcessScreen, 17). The text sits on
# the label node itself, not a child.
_label = (AppiumBy.XPATH, '//*[contains(@resource-id, "ProcessScreen.label") and @text!=""]')


def present(driver, timeout: float = 2) -> bool:
    """Is procivis showing a failed outcome? The `error` probe for base/screens.py.

    The process screen alone is not an error — it is also what the wallet shows while it works. The
    failure animation is the signal, which is why this reuses `_error` rather than SCREEN_ID.
    """
    return wait_present(driver, _error, timeout=timeout)


def message(driver) -> str:
    """procivis's own error copy, read without touching the screen. "" when it is not showing one.

    Read-only on purpose. The Code/Message/Cause behind the "?" is richer, but collecting it means
    tapping through a screen a test has just failed on — that belongs in `wait_for_result`, at the
    moment of failure, and not in a teardown-time reader. `record_error_screen` calls this after the
    fact and explicitly does no UI interaction; see its docstring on why paradym's scrape was both
    fragile and destructive.

    Gated on `present` so a processing screen's label is never published as an error.
    """
    if not present(driver, timeout=1):
        return ""
    found = driver.find_elements(*_label)
    return (found[0].get_attribute("text") or "").strip() if found else ""


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

        # Tap info (the "?" in the header) to open the details screen and collect Code/Message/Cause.
        details: dict = {}
        try:
            self.click(_info)
            # Expanding the cause is best-effort and guarded *separately*: not every error carries
            # an expandable cause, and one absent button must not cost us the Code and Message that
            # are already on screen. Folded into the outer try, a missing expand button discarded
            # the entire details screen and the block fell back to "no details available".
            try:
                self.click(_show_more)
            except Exception:
                logger.debug("[credential_accept] Details screen has no expandable cause")
            details = InvitationErrorDetailsPage(self.driver, **self._page_kwargs()).collect_details()
        except Exception as exc:
            # Never fatal — this runs on a screen a test has already failed on, and the failure
            # being described matters more than the description. But say so: a silent `pass` here
            # is indistinguishable from a wallet that genuinely reported nothing.
            logger.warning(f"[credential_accept] Could not read the error details screen: {exc}")
        # Return to InvitationProcessScreen before raising so teardown can close it cleanly
        try:
            self.driver.back()
        except Exception:
            pass

        detail_str = ", ".join(f"{k}: {v}" for k, v in details.items()) if details else "no details available"
        logger.error(f"[credential_accept] Issuance failed — {detail_str}")
        raise RuntimeError(f"Credential issuance failed — {detail_str} [no_retry]")

    def _page_kwargs(self) -> dict:
        return {"timeouts": self.timeouts, "debug": self.debug}
