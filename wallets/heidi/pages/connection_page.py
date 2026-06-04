from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Heidi shows a connection-consent screen before issuance/verification. Two variants:
#   - trusted issuer/verifier:   title "Connection",           accept button "CONNECT"
#   - untrusted issuer/verifier: title "Untrusted Connection", accept button "CONNECT ANYWAY"
# Both share the "CONNECT WITH:" header, so we anchor the screen on that and accept
# whichever button is present — the same flow handles both.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="CONNECT WITH:"]')

# The TextView itself is not clickable; use the closest clickable ancestor.
# [last()] picks the innermost (most specific) matching View in document order.
_CONNECT = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" and ('
    './/android.widget.TextView[@text="CONNECT"] or '
    './/android.widget.TextView[@text="CONNECT ANYWAY"])])[last()]')
_DECLINE = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="DECLINE"]])[last()]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class ConnectionPage(BasePage):
    def connect(self):
        """Accept the connection.

        Clicks "CONNECT" on a trusted connection or "CONNECT ANYWAY" on an
        untrusted one — whichever button this screen variant shows.
        """
        self.find(_CONNECT).click()

    def decline(self):
        self.find(_DECLINE).click()
