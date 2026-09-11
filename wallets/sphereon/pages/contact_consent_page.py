"""Sphereon's "do you recognize this party" gate, and the extra modal it raises for a low-trust one.

Captured live 2026-09-10 on 0.9.0 (build 901) against four different issuers and one verifier.
The wallet shows this before it will talk to a party it has no contact for:

    Do you recognize <party>
    This is a first interaction with this party. Do you want to continue?
    <federation trust view>  "<party> has a high/low level of trust"
    Details / Name / Description ...
    [Yes, continue]  [Abort]

For a party the wallet does not consider trusted, tapping "Yes, continue" opens a confirmation
modal *on top of* that screen:

    Are you sure you want to continue?
    You are going to interact with a party that has a low level of trust.
    [No, abort]  [Yes, continue]

**Both screens spell their confirm button "Yes, continue", so while the modal is up the bare
content-desc matches two different buttons** — the page's one underneath and the modal's one. Taps
then land on the covered button and nothing happens, which reads exactly like a broken locator. The
modal's button is identified by the company it keeps: it is the only "Yes, continue" that has
"No, abort" as a preceding sibling.

It is serviced as an interstitial rather than a step because it is not tied to any point in a flow:
it appears for a party the wallet has not met, at whatever moment the wallet resolves who it is
talking to, and not at all for one it has. Both were observed for `hovi_issuer` — the gate appeared
again on a second attempt even though the first had already created the contact.

`Findynet` (sphereon_issuer) came back high-trust and raised no modal; Kela, omakela and waltid all
came back low-trust and raised it.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[starts-with(@text,"Do you recognize")]')

_PAGE_CONFIRM = (AppiumBy.XPATH, '//android.widget.Button[@content-desc="Yes, continue"]')
_ABORT = (AppiumBy.XPATH, '//android.widget.Button[@content-desc="Abort"]')

MODAL_ID = (AppiumBy.XPATH, '//*[@text="Are you sure you want to continue?"]')

_MODAL_CONFIRM = (AppiumBy.XPATH,
                  '//android.widget.Button[@content-desc="Yes, continue"]'
                  '[preceding-sibling::android.widget.Button[@content-desc="No, abort"]]')
_MODAL_ABORT = (AppiumBy.XPATH, '//android.widget.Button[@content-desc="No, abort"]')


def on_screen(driver, timeout: float = 1) -> bool:
    """True if the trust gate is showing. Also true while its modal is up, since the modal
    overlays it rather than replacing it — so `modal_on_screen` is checked first by callers."""
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def modal_on_screen(driver, timeout: float = 1) -> bool:
    return wait_present(driver, MODAL_ID, timeout=timeout)


class ContactConsentPage(BasePage):
    def confirm(self):
        """Accept the first interaction with this party."""
        self.click(_PAGE_CONFIRM)

    def confirm_low_trust(self):
        """Accept the extra warning the wallet raises for a party outside its trust federations."""
        self.click(_MODAL_CONFIRM)

    def abort(self):
        self.click(_ABORT)

    def abort_low_trust(self):
        self.click(_MODAL_ABORT)
