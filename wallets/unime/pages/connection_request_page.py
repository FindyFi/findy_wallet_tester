from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# unime's "Connection Request" screen, shown before the information request when the verifier is
# a domain the wallet has not connected to. Seen with procivis's verifier
# (reports/2026-11-20_14-40-17): "Connection Request", the domain, "Unverified Domain",
# "New connection", "Only accept new connections that you recognize and trust", then the buttons
# "Accept connection" and "Reject". It waits for a tap, so a flow that only waits for the
# "Verification Request" screen stalls here and reads as `processing`.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Connection Request"]')

_ACCEPT = (AppiumBy.XPATH, '//*[@text="Accept connection"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class ConnectionRequestPage(BasePage):
    def accept(self):
        self.click(_ACCEPT, timeout=2)
