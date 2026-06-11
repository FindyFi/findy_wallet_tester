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
