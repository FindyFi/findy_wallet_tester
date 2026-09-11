"""Sphereon's credential offer screen — the one screen in this wallet that is NOT captured live.

⚠️ **Unproven locators.** No configured issuer has ever got this far: on 2026-09-10 all five
(sphereon, hovi, procivis, paradym, waltid) failed between the trust gate and the offer, so the
screen has never been on the device in front of us. See `flows/credential_flow.py` for what each
one did.

What is below is not invented, though. It is read out of the build under test — the English copy in
`assets/index.android.bundle` of 0.9.0 (build 901) — and cross-checked against the wallet's own
in-app help text, which describes the issuance flow as:

    3. Scan the issuer's QR code (contact created if first time).
    4. Select the credential to receive.
    5. Enter the issuer's PIN if required (not your wallet's PIN).
    6. Review the credential offer.
    7. Accept or decline the credential (confirmation appears upon acceptance).

Two consequences worth knowing before trusting a red cell that mentions this file:

- **Step 4 and step 5 are not implemented.** A multi-credential offer opens a selection screen
  (`credential_select_type_title` in the bundle) and an offer with a transaction code asks for the
  issuer's PIN. Neither has been seen, and neither is guessed at here; both would surface as
  `absent` — "the offer screen never appeared" — which is honest but not the whole story.
- The heading is matched on "Offered data" (`offered_data_title`) and the buttons on Accept /
  Decline, which is how they are spelled everywhere else in this wallet — "Decline" is the proven
  spelling on the information request screen. If the real screen words any of these differently,
  this detection goes quiet and the flow reports `absent` rather than failing loudly. That is the
  first thing to check when an issuer finally gets past the token endpoint.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Offered data"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@content-desc="Accept" or @text="Accept"]')
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Decline" or @text="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_ACCEPT, timeout=self._get_timeout("credential_offer"))

    def decline(self):
        self.click(_DECLINE)
