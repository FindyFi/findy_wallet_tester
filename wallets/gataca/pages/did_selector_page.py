from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# "My identities DIDs" screen opened by tapping the DID alias button on home.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="My identities DIDs"]')

# The DID alias button on the home screen — a Button showing the current active alias name.
# It's the only Button with a non-empty, non-"Add" content-desc on the home credentials tab.
HOME_DID_ALIAS_BTN = (AppiumBy.XPATH,
    '//android.widget.Button[@clickable="true" and @content-desc!="" and @content-desc!="Add"]'
)

# Confirmation text shown after a DID switch completes.
_ACTIVATED_TEXT = (AppiumBy.XPATH, '//*[contains(@text, "is now active")]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def find_ebsi_alias(driver):
    """Return the first EBSI alias element (content-desc starts with 'Ebsi'), or None."""
    try:
        els = driver.find_elements(
            AppiumBy.XPATH, '//*[starts-with(@content-desc, "Ebsi") and @clickable="true"]'
        )
        return els[0] if els else None
    except Exception:
        return None
