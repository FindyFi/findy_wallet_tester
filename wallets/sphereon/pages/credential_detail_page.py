"""A credential's detail screen: its claims, its issuer, and the menu that is not a delete.

Captured live 2026-09-10 on 0.9.0 (build 901) by opening the wallet's own identity credential from
the Credentials tab. No authentication is asked for on the way in.

    <credential name>                    [Go back]  [More actions button icon]
    <card image>
    Card information            [Hide values] [Show values]
    [Valid ▼]
    First name    Findy
    Email         info@findy.fi
    subject       Findy Test
    Last name     Test
    Issued by     Findy Test
    [About <issuer>]  [Card activities]

The screen is anchored on "Card information" rather than on the list's content-desc: that desc is
built from the credential's type ("SphereonWalletIdentityCredential details"), so it differs per
credential, while this section header is the same on every one.

**There is no delete here, at least not for this credential.** "More actions" opens a menu whose
only item is "Raw credential"; the bundle carries a `credential_delete_wallet_identity_message`
string, which suggests the wallet treats its own identity card specially, so a delete may well
appear for an issued credential. Nobody can say until one is stored — no configured issuer has
managed it — so this page offers no delete and sphereon has no cleanup flow. It is the second
wallet after toppan with no in-app path to an empty wallet, and unlike toppan a wipe does not give
one either: onboarding re-issues the identity card.

Claims are readable, which makes sphereon one of the few wallets where "what was issued" is
answerable rather than only "how many". Nothing asserts on them yet.

**Nothing imports this module.** In the other seven wallets that have one, `flows/cleanup_flow.py`
is the importer — sphereon has no cleanup flow and should not get one, because it has no delete to
drive. So this is a staged capture: the screen was walked on 2026-09-10 and its locators written
down while the wallet was in front of us, ready for whichever comes first — a cleanup flow, if an
issuer ever succeeds and its credential turns out to carry a delete the identity card does not, or
an assertion on issued claims, which is the fleet-wide gap this page is already equipped for
(`wallets/README.md`, gap 3). Being unimported, it is also unexercised: treat every locator here as
captured-but-unproven-in-a-run, the same standing as the ⚠️ in its matrix row.
"""
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Card information"]')

# One row per claim: the container carries the label as its content-desc, and holds the label and
# the value as separate text nodes beneath it.
_CLAIM_ROW = (AppiumBy.XPATH,
              '//android.view.ViewGroup[@clickable="true"][@content-desc]'
              '[.//android.widget.TextView[string-length(@text)>0]]')

_ISSUED_BY_VALUE = (AppiumBy.XPATH,
                    '//*[@text="Issued by"]/following::android.widget.TextView[1]')

_BACK = (AppiumBy.XPATH, '//*[@content-desc="Go back"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class CredentialDetailPage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("sphereon credential detail screen did not load within timeout")

    def claims(self) -> dict:
        """The credential's claims as {label: value}, for asserting on what was issued.

        Rows whose value cannot be read are skipped rather than recorded as empty, so a caller can
        tell "the wallet shows no such claim" from "the claim is there and blank".
        """
        found = {}
        for row in self.driver.find_elements(*_CLAIM_ROW):
            label = (row.get_attribute("content-desc") or "").strip()
            if not label:
                continue
            values = [
                (el.get_attribute("text") or "").strip()
                for el in row.find_elements(AppiumBy.XPATH, './/android.widget.TextView')
            ]
            value = next((v for v in values if v and v != label), "")
            if value:
                found[label] = value
        return found

    def issuer(self) -> str:
        """The name under "Issued by", or "" when it cannot be read."""
        try:
            return (self.find(_ISSUED_BY_VALUE, timeout=2).get_attribute("text") or "").strip()
        except Exception:
            return ""

    def can_delete(self) -> bool:
        """False, always — and it is a measurement, not a stub.

        The actions menu ("More actions button icon", top right) was opened on the wallet identity
        credential on 2026-09-10 and offered exactly one item, "Raw credential". Re-check this once
        an issuer succeeds: a menu with a delete in it would make a cleanup flow possible.

        Takes no `timeout`, unlike the other seven wallets' `can_delete(timeout=2)`, because there
        is no screen to wait on — the answer is a recorded measurement. `base.cleanup` calls this
        with no arguments, so the signature is still what a cleanup flow would need; restore the
        parameter if this ever becomes a real probe.
        """
        return False

    def close(self):
        """Back out to the Credentials tab."""
        if wait_present(self.driver, _BACK, timeout=2):
            self.click(_BACK)
        else:
            self.driver.back()
