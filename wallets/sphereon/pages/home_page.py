"""Sphereon's home screen: the Credentials tab, and the credential count read off it.

Captured live 2026-09-10 on 0.9.0 (build 901).

    Credentials                                      (header)
    Show: [Revoked] [Expired]        [list] [card]   (filters + layout toggle)
    <credential cards>                               (inside an AbsListView labelled "Credentials")
    [QR code scanner] [Activity feed] [Credentials list] [Contacts list]   (bottom nav)

Two elements are needed to know we are here, not one. The bottom nav is also present on the
credential *detail* screen, and the word "Credentials" appears as the list's content-desc on both,
so home is "the header says Credentials **and** the bottom nav is showing".

**A freshly onboarded wallet already holds one credential.** Onboarding self-issues a "Sphereon
Wallet Identity" card carrying the name and email that were typed into it, so a wiped sphereon
counts 1, not 0. It also has no delete action (its only menu item is "Raw credential"), so no
cleanup flow can empty this wallet — see credential_detail_page.py.
"""
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Credentials"]')

# The bottom navigation, which the wallet renders on its tab screens only — not on the trust
# consent, the information request or the error screen.
_NAV = (AppiumBy.XPATH, '//*[@content-desc="Credentials list"]')

# One node per card, carrying the card's own accessibility summary:
#   "Sphereon Wallet Identity. Issued by: Findy Test, on: 9/10/2026. Expires on: ... Status: valid"
# Anchored inside the credential list so the identical node on the detail screen cannot be counted.
#
# Counting these rather than the container ViewGroups is a deliberate choice: the containers are
# unlabelled and would also match any non-card row the list grows later, while this summary is
# emitted per credential and by the wallet itself.
_CARD = (AppiumBy.XPATH,
         '//android.widget.AbsListView[@content-desc="Credentials"]'
         '//*[contains(@content-desc,"Issued by:")]')

# The card onboarding issues to itself, named in the summary above as
# "Sphereon Wallet Identity. Issued by: <holder name>, ...". Nothing an issuer sends can be
# confused with it: the wallet builds it from the name and email typed during onboarding.
_OWN_IDENTITY_NAME = "Sphereon Wallet Identity"


def on_screen(driver, timeout: float = 2) -> bool:
    if not wait_present(driver, SCREEN_ID, timeout=timeout):
        return False
    return wait_present(driver, _NAV, timeout=0.5)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                lambda d: on_screen(d, timeout=1)
            )
        except TimeoutException:
            raise RuntimeError("Sphereon home screen did not load within timeout")

    def open_credential(self) -> bool:
        """Open the first credential card. False when the wallet shows none.

        Located by locator rather than by a held element, so the caller can repeat it: the list
        recomposes whenever it changes and a cached reference goes stale.
        """
        if not wait_present(self.driver, _CARD, timeout=self._get_timeout("default")):
            return False
        self.click(_CARD)
        return True

    def holds_only_own_identity(self) -> bool:
        """True when every card on the tab is the wallet's own self-issued identity credential.

        Needed because a wiped sphereon is not an empty wallet: onboarding leaves one card behind,
        so a count of 1 does not mean an issuer succeeded. It lets the verification test report
        `nothing_to_present` — issuance failed earlier in this run — instead of blaming the
        verifier for a request the wallet was never stocked to answer.

        Returns False when there is nothing to look at, so a caller can only conclude "only the
        identity card" from a positive answer.
        """
        try:
            cards = self.driver.find_elements(*_CARD)
        except Exception:
            return False
        if not cards:
            return False
        return all(
            (c.get_attribute("content-desc") or "").startswith(_OWN_IDENTITY_NAME)
            for c in cards
        )

    def count_credentials(self) -> int:
        """Return the number of credential cards the Credentials tab is showing.

        Raises `CredentialCountUnavailable` rather than returning 0 when home is not showing: an
        empty tree on the wrong screen counts as zero exactly as convincingly as an empty wallet.

        Two caveats worth knowing before reading a number from here:

        - The tab has "Show: Revoked / Expired" filters, both off by default, so a credential that
          arrives already revoked or expired is held by the wallet and not counted here.
        - Android only lays out the cards that fit, so like every other card-counting wallet in the
          suite this saturates on a long list (gataca capped at ~3, procivis at 11). It has only
          ever been read at 1 here, so the ceiling is unmeasured — keep the wallet near empty.
        """
        if not on_screen(self.driver, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "sphereon: the Credentials tab is not showing, so a count would mean 'could not "
                "look', not 'wallet is empty'"
            )
        try:
            return len(self.driver.find_elements(*_CARD))
        except Exception as e:
            raise CredentialCountUnavailable(f"sphereon: credential card lookup failed: {e}") from e
