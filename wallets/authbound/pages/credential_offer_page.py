from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Captured live on 2026-08-05 by minting an authbound_issuer offer and deeplinking it into the
# open wallet. The screen reads:
#     "The following transaction requires your permission and authentication."
#     ISSUANCE REQUEST / Authbound Issuer / wants to add the following / Pension Credential
# with an "Add" button pinned to the bottom.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/document_offer_screen_root")

# The "Add" label itself carries no resource-id — the button container does, so target that.
# Note nothing on this Compose screen reports clickable="true"; the tap lands by coordinates.
_ACCEPT = (AppiumBy.ID, "io.authbound.wallet:id/document_offer_screen_button")

# No explicit decline control: the only way out is the top-left back arrow.
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Go Back"]')

# Header line, kept for diagnostics — it is what distinguishes an issuance request from the
# verification request screen, which shares the same layout.
_HEADER = (AppiumBy.ID,
           "io.authbound.wallet:id/document_offer_screen_content_header_description")


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT)

    def decline(self):
        self.click(_DECLINE)
