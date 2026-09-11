"""Sphereon's presentation request screen.

Captured live 2026-09-10 on 0.9.0 (build 901) against `hovi_verifier`:

    Information request
    <verifier>                                        (contact card)
    The following information will be shared
    No Available Credentials  [Show values]  [0 available]
    [Share]
    [Decline]

**Share is rendered whether or not the wallet can satisfy the request** — with nothing available it
is present in the tree but not clickable, so a flow that only waits for it would tap into the void
and report a locator problem. `no_match_page` is what tells the two apart, and the flow checks it
before touching Share.

The bottom navigation is absent here, which is what keeps `home_page.on_screen` false while this
screen is up — the shared wait loop would otherwise call a request screen `dismissed`.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Information request"]')

_SHARE = (AppiumBy.XPATH, '//*[@content-desc="Share"]')
_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Decline"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationRequestPage(BasePage):
    def share(self):
        self.click(_SHARE, timeout=self._get_timeout("credential_offer"))

    def decline(self):
        self.click(_DECLINE)
