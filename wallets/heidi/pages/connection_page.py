from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Heidi shows a connection screen before issuance/verification. Three variants, not two:
#   - trusted issuer/verifier:   title "Connection",           accept button "CONNECT"
#   - untrusted issuer/verifier: title "Untrusted Connection", accept button "CONNECT ANYWAY"
#   - refused:                   title "Untrusted Connection", subtitle "A connection cannot be
#                                established.", peer marked "Not trustworthy", and the ONLY
#                                control is CLOSE — there is nothing to accept.
#
# All three share the "CONNECT WITH:" header, so that header alone cannot tell them apart. The
# refusal must therefore be checked *before* the consent screen, or heidi's own refusal to talk to
# an issuer is mistaken for a prompt: on 2026-08-27 every issuer hit this, the flow logged
# "Connection consent screen — accepting", found no CONNECT button, and the run published a bare
# `TimeoutException` with no cause for all of them.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="CONNECT WITH:"]')

# The refusal. Anchored on the subtitle rather than on "Not trustworthy", because the subtitle
# states the outcome ("cannot be established") while the trust label is a property of the peer
# that could plausibly appear alongside a CONNECT ANYWAY button.
REFUSED_ID = (AppiumBy.XPATH, '//*[@text="A connection cannot be established."]')

_CLOSE = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="CLOSE"]])[last()]')

# The peer and domain heidi refused, for the failure message.
_DETAIL = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="CONNECT WITH:"]/following::android.widget.TextView')

# The TextView itself is not clickable; use the closest clickable ancestor.
# [last()] picks the innermost (most specific) matching View in document order.
_CONNECT = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" and ('
    './/android.widget.TextView[@text="CONNECT"] or '
    './/android.widget.TextView[@text="CONNECT ANYWAY"])])[last()]')
_DECLINE = (AppiumBy.XPATH,
    '(//android.view.View[@clickable="true" '
    'and .//android.widget.TextView[@text="DECLINE"]])[last()]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def refused(driver, timeout: float = 2) -> bool:
    """True if heidi is refusing the connection outright (no accept button on screen)."""
    return wait_present(driver, REFUSED_ID, timeout=timeout)


class ConnectionPage(BasePage):
    def connect(self):
        """Accept the connection.

        Clicks "CONNECT" on a trusted connection or "CONNECT ANYWAY" on an
        untrusted one — whichever button this screen variant shows.
        """
        self.find(_CONNECT).click()

    def decline(self):
        self.find(_DECLINE).click()

    def refusal_detail(self) -> str:
        """The peer/domain heidi named when refusing, for the failure message."""
        try:
            texts = [el.get_attribute("text") for el in self.driver.find_elements(*_DETAIL)]
            return " | ".join(t for t in texts if t and t not in ("CLOSE",))
        except Exception:
            return "<could not read connection detail>"

    def close(self):
        """Dismiss the refusal screen."""
        self.find(_CLOSE).click()
