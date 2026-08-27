from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# hovi's answer when a verifier asks for something the wallet does not hold:
#
#     You have received an information request from / Unknown Verifier
#     [ No Credential Found ]
#     The credential is not present in your wallet.
#     [ Ok ]
#
# Not a dialog over the request screen. It *is* the request screen's body, so the heading and this
# card sit in the tree together, and a flow that only checks the heading waits out its timeout on an
# `Accept` button that is never coming.
#
# Different from an empty wallet: `procivis_verifier` hit this on 2026-08-20 holding a credential
# that had satisfied `hovi_verifier` minutes earlier.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="No Credential Found"]')

_OK = (AppiumBy.XPATH, '//*[@content-desc="Ok"]')


def present(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def dismiss(driver, timeout: float = 2) -> bool:
    """Tap Ok to leave the request. Returns True if the card is gone afterwards.

    Called before failing, so the next case does not start by fighting its way off this screen.
    """
    if not present(driver, timeout=timeout):
        return True
    try:
        driver.find_element(*_OK).click()
    except Exception:
        return False
    return not present(driver, timeout=2)
