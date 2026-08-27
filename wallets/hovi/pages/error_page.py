from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# hovi's only observed failure message, shown when it cannot process a deeplink or an offer:
#
#     "Please check if the QR is correct and try again. If the problem persists, please contact
#      support."
#
# Despite this module's name it is not a page. It is a clickable ViewGroup pinned to the top of the
# screen (bounds [0,35][720,230] on a 720px device) overlaying whatever hovi is showing: the empty
# home screen, home with credentials, or the processing spinner. It is not specific to one flow
# either. On 2026-08-26 it appeared on three of five issuance failures (authbound, procivis,
# sphereon) and one verification failure (waltid).
#
# The ViewGroup's content-desc and its child TextView's text both carry the message, so the locator
# below is anchored on the copy and nothing structural. A wording change in a new build would
# silence hovi's error detection without failing anything, which makes this the first thing to
# re-check after a wallet update.
#
# How long it stays up is unsettled, and the callers are built for the pessimistic case. It was once
# seen still on screen minutes later, so the flows compare presence before and after firing a
# deeplink and count only a newly appeared message as this test's failure. But `dismiss()` has never
# actually fired in a run: `present()` is False at the start of every case, including reruns that
# begin seconds after one ended with the banner up. That looks more like an ordinary timed toast
# expiring during teardown. The before/after guard costs one 0.5s poll and is correct either way, so
# it stays; just do not trust the "survives until restart" reading without re-checking it.
MESSAGE = (AppiumBy.XPATH, '//*[starts-with(@text,"Please check if the QR")]')


def present(driver, timeout: float = 1) -> bool:
    return wait_present(driver, MESSAGE, timeout=timeout)


def dismiss(driver, app_package: str, timeout: float = 1) -> bool:
    """Restart the app to clear the banner. Returns True if it is gone afterwards.

    Only reached if a banner is already up when a case starts, which no run has yet produced (see
    the note above). It exists so that a banner left over from an earlier case cannot make this one
    look rejected.

    A restart rather than a tap, because the banner is a clickable ViewGroup of unknown
    destination and tapping it can leave the run on some other screen. The cold start costs hovi
    nothing: no lock screen, no session to re-establish.
    """
    if not present(driver, timeout=timeout):
        return True

    driver.terminate_app(app_package)
    driver.activate_app(app_package)
    return not present(driver, timeout=2)


def message(driver, timeout: float = 1) -> str:
    """The error text if it is showing, else an empty string."""
    if not present(driver, timeout=timeout):
        return ""
    try:
        return (driver.find_element(*MESSAGE).get_attribute("text") or "").strip()
    except Exception:
        return "Please check if the QR is correct and try again"
