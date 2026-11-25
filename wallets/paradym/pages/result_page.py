"""The "Go to wallet" button that ends both of paradym's flows, on success and on failure alike.

After an accepted offer or a shared presentation, paradym shows a screen whose button reads "Go to
wallet" — found within about a second of Accept in passing runs (nightly 2026-10-27, appium.log
02:57:10–02:57:11, paradym_issuer). No passing test saves a dump, so the rest of that screen has
never been captured.

What *has* been captured is that paradym's error screen carries the same button: every one of the
300-odd error dumps since 2026-06-22 has `content-desc="Go to wallet,  "` beside "Something went
wrong". So the button is success only when the error heading is not on screen with it.
"""
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

from base.utils import wait_present

GO_TO_WALLET = (AppiumBy.XPATH, '//*[contains(@content-desc, "Go to wallet")]')


def go_to_wallet_on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, GO_TO_WALLET, timeout=timeout)


def go_to_wallet_now(driver) -> bool:
    """Whether Go to wallet is up right now: one lookup, no wait. For teardown, which meets the
    screen already rendered."""
    return bool(driver.find_elements(*GO_TO_WALLET))


def go_to_wallet(driver) -> bool:
    """Tap Go to wallet. False when the button is no longer there to tap; any other driver error,
    a dead session among them, is raised as it is — a missing button is not a lost session."""
    try:
        driver.find_element(*GO_TO_WALLET).click()
    except (NoSuchElementException, StaleElementReferenceException):
        return False
    return True
