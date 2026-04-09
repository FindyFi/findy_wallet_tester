from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Untrusted Connection"]')

# The TextView itself is not clickable; use the closest clickable ancestor.
# [last()] picks the innermost (most specific) matching View in document order.
_CONNECT_ANYWAY = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="CONNECT ANYWAY"]])[last()]')
_DECLINE = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="DECLINE"]])[last()]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class UntrustedConnectionPage(BasePage):
    def connect_anyway(self):
        self.find(_CONNECT_ANYWAY).click()

    def decline(self):
        self.find(_DECLINE).click()
