from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# unime's error surface, in two wordings. Both render *over* the home screen — every home element
# ("Scan", "Activity", "Add") is still in the tree alongside them — so the shared wait loop has to
# check the error surface before it checks home, or unime's refusals read as `dismissed` and blame
# the deeplink instead of the wallet. unime is a Svelte web view and exposes no resource-id on any
# of these TextViews, so the only anchor is the copy itself. A wording change in a new build
# silences this detection without failing anything: first thing to re-check after a unime update.
#
# Current copy (unime 0.19.1, seen 2026-11-20 in 21 dumps of reports/2026-11-20_14-40-17): a toast
# card with a heading and, in the next TextView, a line naming the cause:
#
#     "Couldn't open this request"
#     "UniMe got an answer from core-agent.hovi.id that it couldn't understand. Contact them if
#      this keeps happening."                       (issuance, every issuer; logcat InvalidResponse)
#     "This QR code isn't a request UniMe can handle. Scan a QR code for receiving or sharing
#      credentials."                                (verification; logcat InvalidQrCode)
#
# The heading alone is specific enough to anchor on. The cause line is read, not matched, because
# its wording varies with the cause and the counterparty.
HEADING = "Couldn't open this request"
_HEADING = f'//*[@text="{HEADING}"]'
_CAUSE = f'{_HEADING}/following-sibling::android.widget.TextView[1]'

# Older builds (found 2026-09-02 in 26 of 94 failure dumps): "Oops!" over "Something went wrong.
# Please try again." Kept for them. Both lines are required, because "Oops!" alone is a plausible
# string elsewhere in a wallet and the message alone is generic.
_OLD_HEADING = '//*[@text="Oops!"]'
_OLD_MESSAGE = '//*[starts-with(@text,"Something went wrong")]'

SCREEN_ID = (AppiumBy.XPATH, f"{_HEADING} | {_OLD_HEADING} | {_OLD_MESSAGE}")


def _present_old(driver, timeout: float) -> bool:
    if not wait_present(driver, (AppiumBy.XPATH, _OLD_MESSAGE), timeout=timeout):
        return False
    return wait_present(driver, (AppiumBy.XPATH, _OLD_HEADING), timeout=0.2)


def present(driver, timeout: float = 1) -> bool:
    """True if unime's error surface is showing, in either wording.

    Matched on the words, because unime exposes no resource-id anywhere on this screen — so a copy
    change reads as this detection going quiet, not as a failure. Say so in any message built
    from it.
    """
    if wait_present(driver, (AppiumBy.XPATH, _HEADING), timeout=timeout):
        return True
    return _present_old(driver, timeout=0.2)


def _text(driver, xpath: str) -> str:
    try:
        return " ".join((driver.find_element(AppiumBy.XPATH, xpath).get_attribute("text")
                         or "").split())
    except Exception:
        return ""


def message(driver, timeout: float = 1) -> str:
    """The error text if it is showing, else an empty string.

    The current copy reads as its heading and its cause line ("Couldn't open this request: UniMe
    got an answer from …"), so a failure quotes everything unime said. The older copy reads as its
    one message line, as before.
    """
    if wait_present(driver, (AppiumBy.XPATH, _HEADING), timeout=timeout):
        cause = _text(driver, _CAUSE)
        return f"{HEADING}: {cause}" if cause else HEADING
    if _present_old(driver, timeout=0.2):
        return _text(driver, _OLD_MESSAGE) or "Something went wrong. Please try again."
    return ""


def keeps_no_retry(said: str) -> bool:
    """Whether a rerun would only repeat this refusal.

    Yes for the current copy: its cause line says unime could not understand the counterparty's
    answer, or that the link is not a request it handles, and that repeated on every attempt
    whose dump captured it, in all 8 cells that showed it in reports/2026-11-20_14-40-17. The older
    copy's causes were never separated, so it keeps the default of being retried.
    """
    return said.startswith(HEADING)
