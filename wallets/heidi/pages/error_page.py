import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

# Issuance error screen title (Java exception dialog).
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="An error occurred."]')

# Verification error screen title (network/protocol error dialog).
# Structurally identical to SCREEN_ID — same ScrollView, CANCEL button, and error text locators work for both.
VERIFICATION_SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Error"]')

# The CANCEL button: inner TextView has clickable="false"; parent View is clickable="true".
_CANCEL = (AppiumBy.XPATH,
    '//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="CANCEL"]]')

# All TextViews inside the ScrollView that carry meaningful error detail.
_ERROR_TEXTS = (AppiumBy.XPATH, '//android.widget.ScrollView//android.widget.TextView')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def any_error(driver, timeout: float = 2) -> bool:
    """Either of heidi's two error screens — issuance's "An error occurred." or the verification
    "Error" dialog. The flows check both; so does the shared diagnosis."""
    return (wait_present(driver, SCREEN_ID, timeout=timeout)
            or wait_present(driver, VERIFICATION_SCREEN_ID, timeout=timeout))


def _readable(text: str) -> bool:
    return bool(text) and not text.startswith("<")


def message(driver) -> str:
    """The error screen's own copy, or "" when none is showing or it cannot be read. Read-only."""
    if not any_error(driver, timeout=1):
        return ""
    text = ErrorPage(driver).get_error_text()
    return text if _readable(text) else ""


def quoted(text: str) -> str:
    """': "<copy>"' for heidi's own words; ' (<placeholder>)' when they could not be read.

    The app.log digest publishes the first double-quoted span of a failure as what the wallet
    said, so `get_error_text`'s "<could not read error text>" must never be put in quotes.
    """
    if not text:
        return " (no error text)"
    if not _readable(text):
        return f" ({text.strip('<>')})"
    return f': "{text}"'


class ErrorPage(BasePage):
    def get_error_text(self) -> str:
        """Collect all non-trivial text from the error ScrollView."""
        try:
            elements = self.driver.find_elements(*_ERROR_TEXTS)
            texts = [
                el.get_attribute("text")
                for el in elements
                if el.get_attribute("text") and len(el.get_attribute("text")) > 3
                # The dialog's own button, not part of what heidi said.
                and el.get_attribute("text") != "CANCEL"
            ]
            return " | ".join(texts)
        except Exception:
            return "<could not read error text>"

    def cancel(self):
        self.find(_CANCEL).click()
