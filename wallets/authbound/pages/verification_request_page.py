from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Captured live 2026-08-05 from a failing run's page dump. The screen mirrors the issuance
# consent screen's layout:
#     "The following transaction requires your permission and authentication."
#     DATA SHARING REQUEST / Relying Party / requests the following
# with a "Share" button pinned to the bottom and a back arrow top-left.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/request_screen_root")

# Shown *inside* the request screen when the verifier asks for a credential the wallet does
# not hold: an error icon above "The requested document is not available in your wallet".
# The Share button still renders, so this has to be checked before sharing.
NO_MATCHING_CREDENTIALS_ID = (AppiumBy.ID, "io.authbound.wallet:id/request_screen_empty_state")

# One container per document the wallet will present, `request_screen_requested_document_<n>`.
# This count matters for automation, not just for logging: the wallet demands a *separate*
# device authentication per document (measured live 2026-08-12 — six listed documents needed
# seven authentications before the presentation went through). The count grows with the wallet's
# contents, which is why sharing used to finish after one prompt and now needs many.
REQUESTED_DOCUMENTS = (AppiumBy.XPATH,
                       '//*[starts-with(@resource-id, '
                       '"io.authbound.wallet:id/request_screen_requested_document_")]')

# The "Share" label carries no id of its own — the button container does.
_SHARE = (AppiumBy.ID, "io.authbound.wallet:id/request_screen_button")

# No explicit decline control: the only way out is the top-left back arrow.
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Go Back"]')

# Header line, kept for diagnostics — it is what distinguishes this from the issuance
# consent screen, which shares the same layout.
_HEADER = (AppiumBy.ID, "io.authbound.wallet:id/request_screen_content_header_description")


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def requested_document_count(self) -> int:
        """How many documents the wallet is about to present.

        Each one costs a separate device authentication, so the caller uses this to budget how
        many prompts to answer instead of guessing.
        """
        try:
            return len(self.driver.find_elements(*REQUESTED_DOCUMENTS))
        except Exception:
            return 0

    def share(self):
        self.click(_SHARE)

    def decline(self):
        self.click(_DECLINE)
