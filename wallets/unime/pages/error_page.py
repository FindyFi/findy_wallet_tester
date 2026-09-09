from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# unime's only observed failure surface:
#
#     "Oops!"
#     "Something went wrong. Please try again."
#
# Found 2026-09-02 by mining 94 historical failure dumps: it is present in 26 of them, across
# waltid (9), sphereon (6), procivis (6) and authbound (4) issuance. Every one of those was
# published as a bare timeout ("No recognisable screen appeared after deeplink"), because nothing
# in the suite had ever looked for this screen. unime was the last wallet with no error detection
# at all.
#
# Despite the module name it is not a page. Like hovi's banner it renders *over* the home screen —
# every home element ("Scan", "All", "Data", "Badges") is still in the tree alongside it. That is
# why the shared wait loop must check the error surface before it checks home: otherwise unime's
# rejections read as `dismissed`, which blames the deeplink instead of the wallet.
#
# unime is a Svelte web view and exposes no resource-id on either TextView, so the only anchor is
# the copy itself. A wording change in a new build would silence this detection without failing
# anything — first thing to re-check after a unime update.
_HEADING = '//*[@text="Oops!"]'
_MESSAGE = '//*[starts-with(@text,"Something went wrong")]'

# Both, because "Oops!" alone is a plausible string elsewhere in a wallet and the message alone is
# generic. Requiring the pair keeps this specific to the screen actually observed.
SCREEN_ID = (AppiumBy.XPATH, f"{_HEADING} | {_MESSAGE}")


def present(driver, timeout: float = 1) -> bool:
    """True if unime's error overlay is showing.

    Matched on the words "Oops!" / "Something went wrong", because unime exposes no resource-id
    anywhere on this screen — so a copy change reads as this detection going quiet, not as a
    failure. Say so in any message built from it.
    """
    if not wait_present(driver, (AppiumBy.XPATH, _MESSAGE), timeout=timeout):
        return False
    return wait_present(driver, (AppiumBy.XPATH, _HEADING), timeout=0.2)


def message(driver, timeout: float = 1) -> str:
    """The error text if it is showing, else an empty string."""
    if not present(driver, timeout=timeout):
        return ""
    try:
        return (driver.find_element(AppiumBy.XPATH, _MESSAGE).get_attribute("text") or "").strip()
    except Exception:
        return "Something went wrong. Please try again."
