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

TERMINAL_ID = (AppiumBy.XPATH, _TERMINAL)
LOGIN_ID = (AppiumBy.XPATH, _LOGIN)

_OK = (AppiumBy.XPATH, '//*[@content-desc="OK" or (@text="OK" and @clickable="true")]')

# The dialog gataca shows when a presentation went through, measured on the device: the title is
# exactly "Verification Complete", the body "Now you can go to the services page and access to your
# content", with an "OK" and a close mark, over the dimmed home list. The casing variant is kept in
# case a build changes it. Because "My credentials" is still in the tree beneath the modal, a flow
# must look for this *before* home (see verification_flow).
_COMPLETE = ('//*[@text="Verification Complete" or @content-desc="Verification Complete"'
             ' or @text="Verification complete" or @content-desc="Verification complete"]')
COMPLETE_ID = (AppiumBy.XPATH, _COMPLETE)

# The banner gataca's home shows after an issuance ("Credential Successfully Issued", seen on 5
# archived runs and on the 2026-11-18 procivis run's final dump). A banner is not a stored
# credential, so it is evidence for the trail, never for the count.
ISSUED_BANNER_ID = (AppiumBy.XPATH, '//*[@text="Credential Successfully Issued"]')

# The verifier's own "Rejected" dialog (hovi_verifier, 2026-11-18): a heading, the verifier's host
# and an OK, nothing else.
REJECTED_ID = (AppiumBy.XPATH, '//*[@text="Rejected"]')
_REJECTED_TEXTS = (AppiumBy.XPATH, '//android.widget.TextView[string-length(@text) > 0]')


def on_terminal_screen(driver, timeout: float = 2) -> bool:
    """"Credentials Shared" — the credential was issued or the presentation was sent."""
    return wait_present(driver, TERMINAL_ID, timeout=timeout)


def on_login_screen(driver, timeout: float = 2) -> bool:
    """"Login Successful" — the OIDC step is done. Says nothing about the credential."""
    return wait_present(driver, LOGIN_ID, timeout=timeout)


def on_complete_screen(driver, timeout: float = 2) -> bool:
    """"Verification Complete" — the presentation was accepted. Probe it before home."""
    return wait_present(driver, COMPLETE_ID, timeout=timeout)


def on_issued_banner(driver, timeout: float = 2) -> bool:
    """The home banner "Credential Successfully Issued". Says nothing about the count."""
    return wait_present(driver, ISSUED_BANNER_ID, timeout=timeout)


def on_rejected_screen(driver, timeout: float = 2) -> bool:
    """The verifier's "Rejected" dialog."""
    return wait_present(driver, REJECTED_ID, timeout=timeout)


def rejected_detail(driver) -> str:
    """The texts of the "Rejected" dialog other than its heading and OK — the verifier's host
    (hovi: "core-agent.hovi.id"), and a body if a verifier ever sends one. "" when unreadable.
    Never raises."""
    try:
        texts = [(e.get_attribute("text") or "").strip()
                 for e in driver.find_elements(*_REJECTED_TEXTS)]
    except Exception:
        return ""
    return " ".join(t for t in texts if t and t not in ("Rejected", "OK"))


def tap_rejected_ok(driver) -> bool:
    """Tap OK on the "Rejected" dialog if it is up, with no wait. True if tapped. Never raises."""
    try:
        if driver.find_elements(*REJECTED_ID):
            found = driver.find_elements(*_OK)
            if found:
                found[0].click()
                return True
    except Exception:
        pass
    return False


class SuccessPage(BasePage):
    def confirm(self):
        self.click(_OK)
