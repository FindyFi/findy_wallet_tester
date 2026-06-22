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


def list_aliases(driver):
    """Return the identity labels listed on 'My identities DIDs' (best-effort, for logging).

    Each identity is a clickable row whose content-desc is its alias; the active row's content-desc
    is expanded to "<alias>, <did:...>". Used to see which DIDs already exist before deciding
    whether a new one needs to be created.
    """
    try:
        els = driver.find_elements(
            AppiumBy.XPATH, '//*[@clickable="true" and string-length(@content-desc) > 0]'
        )
        return [e.get_attribute("content-desc") or "" for e in els]
    except Exception:
        return []


def find_alias(driver, alias: str):
    """Return the first identity-alias element whose content-desc starts with `alias`, or None.

    `alias` is the wallet's auto-assigned identity label for a DID method (e.g. "JWK Identity",
    "Ebsi Identity", "Gataca").
    """
    try:
        els = driver.find_elements(
            AppiumBy.XPATH, f'//*[starts-with(@content-desc, "{alias}") and @clickable="true"]'
        )
        return els[0] if els else None
    except Exception:
        return None


def is_alias_active(driver, alias: str, did_prefix: str, timeout: float = 5) -> bool:
    """Return True if the `alias` row shows as the active DID.

    After a DID switch the active row's content-desc expands from alias-only to
    "<alias>, <did:...>", so the identity is active iff its row's content-desc contains both the
    alias label and its did_prefix (e.g. "JWK Identity" and "did:jwk:").
    """
    locator = (AppiumBy.XPATH,
        f'//*[contains(@content-desc, "{alias}") and contains(@content-desc, "{did_prefix}")]'
    )
    return wait_present(driver, locator, timeout=timeout)
