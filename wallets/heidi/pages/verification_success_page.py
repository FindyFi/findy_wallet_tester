from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Heidi's terminal screen after a presentation is sent: "Information Successfully Shared" with a
# single DONE button. It is NOT the home screen, and the wallet stays on it until DONE is tapped.
#
# Waiting for home directly instead of this screen is how a presentation the verifier accepted was
# published as a bare timeout: the flow sat on the success notice until the wait expired. It is
# also the only wallet-side evidence heidi gives that the share completed, so the flow anchors
# success on it rather than on merely returning to home.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Information Successfully Shared"]')

# The TextView carries the label; the clickable node is its nearest clickable ancestor View.
# Matched on button text — heidi is Jetpack Compose and ships no test tags, so there is no id
# to anchor on and a layout change will surface here as "DONE not found".
_DONE = (AppiumBy.XPATH,
         '(//android.view.View[@clickable="true" '
         'and .//android.widget.TextView[@text="DONE"]])[last()]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class VerificationSuccessPage(BasePage):
    def dismiss(self):
        """Tap DONE to close the notice and return heidi to its home screen."""
        self.click(_DONE)
