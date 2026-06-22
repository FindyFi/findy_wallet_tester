from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Gataca shows different success headings depending on the flow:
#   "Login Successful"  — OIDC auth step (verification / some issuers)
#   "Credentials Shared" — credential issued / presentation sent ("…have been authenticated")
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Login Successful" or @text="Credentials Shared"]')
_OK = (AppiumBy.XPATH, '//*[@content-desc="OK" or (@text="OK" and @clickable="true")]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class SuccessPage(BasePage):
    def confirm(self):
        self.click(_OK)
