from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TOPPAN Wallet"]')

# One card View per credential. Toppan is a WebView app, and this locator has now been wrong in
# both directions, so both halves of it are load-bearing.
#
# **Long lists** — Chrome prunes the *descendants* of cards below the scroll viewport while
# keeping the card node itself, so counting anything *inside* a card under-reports. Measured
# 2026-08-05: a wallet showing 13+ cards had 14 card containers in the tree but only 11 still
# carried their "Issued on" line, which is what the count read then. Counting containers fixed it.
#
# **Short lists** — the container was reached via `@scrollable="true"`, and a list that fits on
# screen **does not scroll**, so that flag is absent and the count read **0**. Measured 2026-09-03
# on a freshly wiped wallet holding 3 credentials: zero scrollable nodes in the whole tree, count
# 0, and a perfectly good issuance published as "not stored". The two tree shapes are otherwise
# byte-for-byte identical in structure — the scrollable flag is the only thing that differs — so
# the old locator worked only because toppan's wallet was always dirty. Cleaning it up broke the
# measurement.
#
# So: the scrollable path stays (it needs no text and handles the long case), unioned with a path
# anchored on a rendered card. At least one card is always rendered when the wallet is non-empty,
# and its 2nd `android.view.View` ancestor is the list container in both shapes. An XPath union
# returns each node once, and on a long list both halves select exactly the same nodes — verified
# against the captured dumps: 3 and 14, never 6 or 28.
#
# The [*] predicate skips the empty placeholder View the list keeps after the last card.
#
# The anchor text is English because onboarding always selects English (UK) — see
# `pages/language_page.DEFAULT_LANGUAGE`. Change one and this must change with it.
_credential_card = (AppiumBy.XPATH,
    '//android.view.View[@scrollable="true"]/android.view.View/android.view.View[*]'
    ' | (//*[starts-with(@text,"Issued on")])[1]/ancestor::android.view.View[2]'
    '/android.view.View[*]')



class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credential cards on the home screen.

        Counts card containers, not text inside them — see `_credential_card` for why that
        distinction is what makes the number move at all. Cards are on home, so no navigation,
        but home has to be showing or zero cards would mean "could not look" rather than
        "wallet is empty".

        Counts card containers, so it is no longer bounded by what Chrome renders inside them,
        and no longer depends on the list being scrollable — see `_credential_card` for the two
        opposite ways this went wrong. toppan has no in-app delete (measured 2026-09-03), so the
        only way to trim the wallet is the app wipe (`onboarding.reset`).
        """
        if not wait_present(self.driver, SCREEN_ID, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "toppan: home screen ('TOPPAN Wallet') is not showing, so a count would mean "
                "'could not look', not 'wallet is empty'"
            )
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception as e:
            raise CredentialCountUnavailable(f"toppan: credential card lookup failed: {e}") from e
