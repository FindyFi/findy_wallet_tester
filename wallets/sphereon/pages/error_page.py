"""Sphereon's failure screen, and the wallet's own words for what went wrong.

Captured live 2026-09-10 on 0.9.0 (build 901), from three different failed issuances:

    Retrieve credentials
    Retrieving an access token from https://agent.findynet.demo.sphereon.com/oid4vci/token for
    issuer ... failed with status: 400. Response: {"error":"invalid_request"}
    [Ok, I understand]

    Sending authorization challenge request
    [Ok, I understand]

It is a full screen, not an overlay: nothing from home is in the tree alongside it, and dismissing
it lands back on home. So it is not sticky and needs no before/after comparison — contrast hovi,
whose error surface is a banner that can outlive the case that produced it.

The heading changes with the step that failed and the body is sometimes absent, so **the anchor is
the dismiss button**, whose content-desc ("Ok, I understand. Exit flow") is the one constant across
all three captures. Note the button's *visible* text is only "Ok, I understand" — the desc carries
the extra "Exit flow", which is what makes it specific enough to match on.

Nothing here has a resource-id; the wallet exposes none anywhere.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

_DISMISS = (AppiumBy.XPATH, '//*[@content-desc="Ok, I understand. Exit flow"]')

SCREEN_ID = _DISMISS

# Every text node on the screen. The heading is the failed step and the body, when there is one,
# quotes the issuer's HTTP response verbatim — which is the whole value of reading it: it names the
# party at fault instead of leaving a red cell that says only "the wallet said no".
#
# Toasts are excluded. The wallet's own toast host ("toastAnimatedContainer") outlives the screen
# that raised it, so the first run of this quoted `hovi_issuer` as "Sending authorization challenge
# request — Contact successfully created" — a success notice from the step *before* the failure,
# reading as if it were part of it.
_TEXTS = (AppiumBy.XPATH,
          '//*[string-length(@text)>2]'
          '[not(ancestor-or-self::*[@resource-id="toastAnimatedContainer"])]')

# Dropped from the quoted message: it is our own dismiss affordance, not part of what the wallet
# said about the failure.
_BUTTON_LABEL = "Ok, I understand"


def present(driver, timeout: float = 1) -> bool:
    return wait_present(driver, _DISMISS, timeout=timeout)


def message(driver, timeout: float = 1) -> str:
    """The wallet's error copy — heading and body joined — or "" when the screen is not showing."""
    if not present(driver, timeout=timeout):
        return ""
    try:
        parts = []
        for el in driver.find_elements(*_TEXTS):
            text = (el.get_attribute("text") or "").strip()
            if text and text != _BUTTON_LABEL and text not in parts:
                parts.append(text)
        return " — ".join(parts)
    except Exception:
        return ""


def dismiss(driver, timeout: float = 2) -> bool:
    """Tap "Ok, I understand" to exit the failed flow, returning the wallet to home.

    Used by `init_flow` when it finds the app parked here between tests, so a failed case does not
    leave every later one starting from an unrecognised screen.
    """
    if not present(driver, timeout=timeout):
        return False
    driver.find_element(*_DISMISS).click()
    return True
