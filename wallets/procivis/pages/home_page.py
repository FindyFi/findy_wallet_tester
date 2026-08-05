from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="WalletScreen.header"]')

# TODO: capture the per-credential locator on WalletScreen from a live dump with a credential
# present, then implement count_credentials() below. Procivis uses stable React Native test
# IDs (e.g. "CredentialOfferScreen.accept"), so the wallet list almost certainly exposes one
# per credential — it just hasn't been read off a device yet.


class HomePage(BasePage):
    def wait_until_loaded(self, timeout=None):
        self.find(SCREEN_ID, timeout=timeout)

    def count_credentials(self) -> int:
        """Not implemented yet — procivis cannot report a credential count.

        This is the only wallet that never had a `count_credentials()` at all. It exists now so
        that callers get the same explicit "count unavailable" answer as every other wallet
        rather than an AttributeError.
        """
        raise CredentialCountUnavailable(
            "procivis: no credential-list locator has been captured yet, so the wallet cannot "
            "report a count (needs a live dump of WalletScreen with a credential)"
        )
