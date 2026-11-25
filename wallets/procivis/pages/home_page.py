from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="WalletScreen.header"]')

# Procivis is React Native and gives every credential card a stable testID:
#
#     WalletScreen.credential.<uuid>.card
#
# with a family of children under it (.card.header, .card.header.name, .card.header.openDetail, …).
# Matching the exact ".card" suffix counts each credential once and avoids the ~9 child ids per
# card, which also keeps this to a single round trip instead of one per element.
#
# Read off 23 historical WalletScreen dumps on 2026-09-02: distinct card ids tracked the wallet's
# real contents across runs (4 → 6 → 7 → 9 → 10 → 11, then 2 once clean-slate wipes began), and in
# every dump the number of ".card" ids equalled the number of distinct credential uuids.
_CREDENTIAL_CARD = (
    AppiumBy.XPATH,
    '//*[starts-with(@resource-id, "WalletScreen.credential.")'
    ' and substring(@resource-id, string-length(@resource-id) - 4) = ".card"]',
)


# The card's "open detail" affordance. Tapping the card itself only **expands it in place** on the
# wallet screen — it does not navigate — so this sibling testID is what opens the detail screen.
_OPEN_DETAIL = (
    AppiumBy.XPATH,
    '//*[starts-with(@resource-id, "WalletScreen.credential.")'
    ' and substring(@resource-id, string-length(@resource-id) - 10) = ".openDetail"]',
)


def on_screen(driver, timeout: float = 2) -> bool:
    """Is the wallet list in front? The `home` probe for base/screens.py."""
    return wait_present(driver, SCREEN_ID, timeout=timeout)


# procivis's own empty state: "No credentials yet" / "Connect to an issuer to add your first
# credential.", as `WalletScreen.empty.title` and `.subtitle` beside the header. Read off the two
# empty-wallet dumps of 2026-09-18 (15-37-26 and 16-12-02); no dump holding a card shows it.
_EMPTY_STATE = (AppiumBy.XPATH, '//*[@resource-id="WalletScreen.empty.title"]')


def empty_on_screen(driver, timeout: float = 2) -> bool:
    """Is procivis saying, in its own words, that it holds no credentials? Read-only."""
    return wait_present(driver, _EMPTY_STATE, timeout=timeout)


def holds_nothing(driver, count) -> bool:
    """True only when the count is 0 *and* procivis shows its own empty state.

    A 0 on its own is not proof: `count_credentials` reads 0 whenever the header is up and no card
    ids are in the tree yet — a list still rendering looks exactly like an empty wallet. Calling
    that `nothing_to_present` would skip the verifier entirely on a wallet that holds a credential.
    """
    return count == 0 and empty_on_screen(driver, timeout=2)


class HomePage(BasePage):
    def wait_until_loaded(self, timeout=None):
        self.find(SCREEN_ID, timeout=timeout)

    def count_credentials(self) -> int:
        """How many credential cards the wallet list is showing.

        Raises `CredentialCountUnavailable` rather than returning 0 when the wallet screen is not
        in front, because an empty accessibility tree on the wrong screen counts as zero just as
        convincingly as an empty wallet does.

        Known limit: this counts what is in the accessibility tree. The list scrolls
        (`WalletScreen.scroll`), and no dump has yet shown more than 11 cards at once, so whether
        a long list is virtualised is untested. The clean-slate policy keeps wallets at 0 between
        runs, so it has not mattered; a wallet allowed to accumulate could undercount.
        """
        if not wait_present(self.driver, SCREEN_ID, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "procivis: the wallet screen (WalletScreen.header) is not in front, so any count "
                "would describe whatever screen is"
            )
        return len(self.driver.find_elements(*_CREDENTIAL_CARD))

    def open_credential(self) -> bool:
        """Open the first credential's detail screen. False when the wallet shows no card.

        Deleting returns to the wallet with the next card first, so "open the first, delete,
        repeat" is a complete traversal. The card is located per call rather than held, because
        the list recomposes after every delete.
        """
        if not wait_present(self.driver, _OPEN_DETAIL, timeout=self._get_timeout("default")):
            return False
        self.click(_OPEN_DETAIL)
        return True
