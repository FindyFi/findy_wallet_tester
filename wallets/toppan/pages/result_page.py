"""toppan's result screen: the WebView page that ends a verification, with one "Back to Home Page".

Its shape, from the dumps: a View holding an Image, one TextView with toppan's message, and a View
around the "Back to Home Page" button. It ignores BACK — teardown pressed it eight times against
this screen and then restarted the app (test.log of the 2026-09-16 nightly, 00:51:09–00:52:00).
So it is left through its own button: by the verification flow on success, and by init_flow in
teardown on failure, after the failure screenshot and dump have captured it.

What it has been seen to say, and where:

- **before any request**, in place of the verification request:
  "Request docType urn:vc:authbound:pension:1.0 is not available" — nightly 2026-09-14 and
  2026-09-16, authbound_verifier, a wallet holding three credentials from other issuers. Older
  builds said "No document shared" there (reports/2026-08-13_10-20-41/toppan, authbound_verifier).
- **after Share**: "No document shared" — reports/2026-08-13_10-20-41/toppan/test.log, 10:35:57,
  procivis_verifier. (Its dump shows home: the flow had already tapped the button.)
- **after Share, on success**: "Information Shared" / "The following was securely sent". No dump —
  a passing test saves none — but every passing toppan verification's test.log records the old
  flow matching one of them, e.g. the 2026-10-15 nightly "complete — Information Shared".
"""
import logging

from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

logger = logging.getLogger(__name__)

# `//*`, not `//android.widget.Button`, on purpose: it is the locator the old flow matched on
# success screens, which no dump shows. The failure dumps have it as an android.widget.Button.
_BACK = (AppiumBy.XPATH, '//*[@text="Back to Home Page"]')
_MESSAGE = (AppiumBy.XPATH, '//*[@text="Back to Home Page"]/../../android.widget.TextView')
_SHARED = (AppiumBy.XPATH, '//*[@text="Information Shared"]')
_SENT = (AppiumBy.XPATH, '//*[@text="The following was securely sent"]')

_NO_MATCH_HEAD = "Request docType "
_NO_MATCH_TAIL = " is not available"


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, _BACK, timeout=timeout)


def shared(driver, timeout: float = 1) -> bool:
    """toppan's own confirmation that the presentation went out."""
    return (wait_present(driver, _SHARED, timeout=timeout)
            or wait_present(driver, _SENT, timeout=timeout))


def message(driver) -> str:
    """The screen's message on one line, or "" when it cannot be read. Never raises."""
    try:
        texts = [(el.text or "").strip() for el in driver.find_elements(*_MESSAGE)]
    except Exception:
        return ""
    return " ".join(" ".join(t for t in texts if t).split())


def is_no_match(said: str) -> bool:
    """toppan naming a document type it does not hold, in its own words."""
    return said.startswith(_NO_MATCH_HEAD) and said.endswith(_NO_MATCH_TAIL)


def no_match(driver, timeout: float = 2) -> bool:
    return on_screen(driver, timeout=timeout) and is_no_match(message(driver))


def back_home(driver) -> None:
    """Tap "Back to Home Page". Best effort: a failed tap must never replace the verdict already
    reached — init_flow's own recovery still runs afterwards."""
    try:
        driver.find_element(*_BACK).click()
    except Exception as exc:
        logger.warning(f"[result_page] Could not tap Back to Home Page: {exc}")
