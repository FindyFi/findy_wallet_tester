from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# hovi's processing screen: the spinner is not in the accessibility tree at all, so the screen is
# a single clickable `Cancel` and nothing else.
_CANCEL = (AppiumBy.XPATH, '//*[@content-desc="Cancel"]')

# The delete confirmation also has a Cancel, so its title is checked to tell the two apart.
_DELETE_DIALOG = (AppiumBy.XPATH, '//*[@text="Delete Credential?"]')


def on_screen(driver, timeout: float = 1) -> bool:
    """True if hovi is showing its processing screen."""
    if not wait_present(driver, _CANCEL, timeout=timeout):
        return False
    return not wait_present(driver, _DELETE_DIALOG, timeout=0.2)
