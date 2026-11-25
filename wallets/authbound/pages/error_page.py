from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Generic error screen shown when the wallet cannot complete an operation (e.g. a
# credential offer it rejects). Captured live: body reads "An unexpected error has
# occurred. Please try again." with a "Close" button (content-desc).
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/content_error_root")
_CLOSE = (AppiumBy.ACCESSIBILITY_ID, "Close")


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def message(driver) -> str:
    """The error screen's own copy, or "" when it is not showing or cannot be read. Read-only.

    For `screens.py`: a failure message quotes this rather than paraphrasing the wallet, and
    `get_error_text`'s "(no error text)" placeholders must not be published as if the wallet said
    them.
    """
    if not on_screen(driver, timeout=1):
        return ""
    text = ErrorPage(driver).get_error_text()
    return "" if text.startswith("(") else text


def quoted(text: str) -> str:
    """': "<copy>"' for the wallet's own words; ' (<placeholder>)' when they could not be read.

    The app.log digest publishes the first double-quoted span of a failure as what the wallet
    said, so `get_error_text`'s "(no error text)" must never be put in quotes.
    """
    if not text:
        return " (no error text)"
    if text.startswith("("):
        return f" {text}"
    return f': "{text}"'


class ErrorPage(BasePage):
    def get_error_text(self) -> str:
        """Return the concatenated body text of the error screen for diagnostics."""
        try:
            root = self.find(SCREEN_ID)
            texts = [
                e.get_attribute("text")
                for e in root.find_elements(AppiumBy.CLASS_NAME, "android.widget.TextView")
            ]
            return " ".join(t for t in texts if t).strip() or "(no error text)"
        except Exception:
            return "(could not read error text)"

    def close(self):
        self.click(_CLOSE)
