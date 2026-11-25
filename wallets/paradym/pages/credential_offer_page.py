from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

_accept = (AppiumBy.XPATH, '//*[@content-desc="Accept"]')

# paradym's loading text while it fetches an offer from the issuer. No dump or log in reports/ or
# the nightly clone shows it — the old flow looked for it only after its wait had already run out,
# and none ever did. So screens.py leaves `processing` UNKNOWN, and the flow uses this only to say
# that paradym was still fetching when its wait ran out.
_fetching = (AppiumBy.XPATH, '//*[@text="Fetching information"]')


def on_screen(driver, timeout: float = 2) -> bool:
    """The offer itself, by its Accept button."""
    return wait_present(driver, _accept, timeout=timeout)


def fetching_on_screen(driver, timeout: float = 1) -> bool:
    return wait_present(driver, _fetching, timeout=timeout)


class CredentialOfferPage(BasePage):
    def accept(self):
        self.click(_accept)
