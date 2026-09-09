from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Gataca shows two different headings on the same OK dialog, and they mean different things.
# Treating them as one was a live bug: a flow that polled "either heading" could take the
# intermediate one as terminal and report a credential that never arrived.
#
#   "Login Successful"   — the OIDC auth step. **Intermediate on the issuance path**: on an
#                          authorization_code issuer the wallet shows it, takes an OK, raises a
#                          second biometric to sign the credential request, and only then issues.
#   "Credentials Shared" — terminal: the credential was issued, or the presentation was sent.
_TERMINAL = '//*[@text="Credentials Shared"]'
_LOGIN = '//*[@text="Login Successful"]'

# Either heading — for tapping OK, which both dialogs carry. Callers deciding whether a flow is
# *finished* must use `on_terminal_screen`, not this.
SCREEN_ID = (AppiumBy.XPATH, f'{_TERMINAL} | {_LOGIN}')
TERMINAL_ID = (AppiumBy.XPATH, _TERMINAL)
LOGIN_ID = (AppiumBy.XPATH, _LOGIN)

_OK = (AppiumBy.XPATH, '//*[@content-desc="OK" or (@text="OK" and @clickable="true")]')


def on_screen(driver, timeout: float = 2) -> bool:
    """Either dialog is up. Use for "is there an OK to tap", never for "did this succeed"."""
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def on_terminal_screen(driver, timeout: float = 2) -> bool:
    """"Credentials Shared" — the credential was issued or the presentation was sent."""
    return wait_present(driver, TERMINAL_ID, timeout=timeout)


def on_login_screen(driver, timeout: float = 2) -> bool:
    """"Login Successful" — the OIDC step is done. Says nothing about the credential."""
    return wait_present(driver, LOGIN_ID, timeout=timeout)


class SuccessPage(BasePage):
    def confirm(self):
        self.click(_OK)
