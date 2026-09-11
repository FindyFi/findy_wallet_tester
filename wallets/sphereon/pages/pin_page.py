"""Sphereon's PIN entry, which is the same widget on three different screens.

Captured live 2026-09-10 on 0.9.0 (build 901):

    lock screen        "Login to the Sphereon Wallet" / "Enter your pin code."
    onboarding 1/4     "Set a 6-digit Sphereon PIN"
    onboarding 2/4     "Repeat your Sphereon PIN"

**The digits cannot be injected.** There is no `EditText` anywhere in the tree — the six digit
slots are bare `ViewGroup`s and the input behind them is invisible to the accessibility tree — and
both `press_keycode(KEYCODE_0..9)` and `adb shell input text 346399` are accepted by Android and
then ignored by the wallet: the field stays empty and the screen never advances. What does work is
`mobile: type`, one digit per call, after tapping the slots to give the field focus and raise the
IME. Measured that way the screen advances on the sixth digit.

**One digit per call, not the whole PIN.** `mobile: type` with all six characters at once left the
field empty and the screen on 1/4 — the wallet processes a single character per input event — so
this types digit by digit with `timeouts.pin_digit_delay` between them, the same shape as the other
wallets' keypad pages even though there is no on-screen keypad here.

There is no submit button: the wallet acts on the sixth digit by itself.
"""
import time

from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# The lock screen, which is the only one of the three that says whose PIN it wants. Matched on the
# copy of both lines, because "Enter your pin code." alone is generic enough to appear elsewhere.
HEADING = (AppiumBy.XPATH, '//*[starts-with(@text,"Login to")]')

# The PIN slots, in the two shapes they have been seen in. The lock screen labels the container
# "Pin code"; the onboarding screens label nothing, so there the anchor is the only
# clickable-and-focusable ViewGroup on the screen. Tried in order, since the first is the specific
# one; a union XPath would return them in document order instead.
_FIELD_LOCATORS = (
    (AppiumBy.XPATH, '//android.view.ViewGroup[@clickable="true"][.//*[@content-desc="Pin code"]]'),
    (AppiumBy.XPATH, '//android.view.ViewGroup[@clickable="true" and @focusable="true"]'),
)


def on_screen(driver, timeout: float = 2) -> bool:
    """True if the *lock* screen is showing.

    Deliberately not true for the onboarding PIN screens: `init_flow` reaches those by walking
    onboarding and must not mistake "set a PIN" for "unlock with the PIN I already have".
    """
    return wait_present(driver, HEADING, timeout=timeout)


class PinPage(BasePage):
    def _focus_field(self) -> bool:
        """Tap the digit slots so the field takes focus and the IME comes up.

        Returns False when neither shape of the field is on screen, which the caller reports —
        typing into an unfocused field silently does nothing, and that failure otherwise surfaces
        as "the screen never advanced".
        """
        for locator in _FIELD_LOCATORS:
            if wait_present(self.driver, locator, timeout=1):
                self.click(locator)
                time.sleep(0.8)  # the IME animating in; a digit typed before it lands is dropped
                return True
        return False

    def enter_pin(self, pin: str):
        if not self._focus_field():
            raise RuntimeError(
                "sphereon: no PIN field on screen, so the PIN could not be typed. "
                f"Looked for {_FIELD_LOCATORS[0][1]} and {_FIELD_LOCATORS[1][1]}"
            )
        delay = self._get_timeout("pin_digit_delay", 0.4)
        for digit in str(pin):
            self.driver.execute_script("mobile: type", {"text": digit})
            time.sleep(delay)
