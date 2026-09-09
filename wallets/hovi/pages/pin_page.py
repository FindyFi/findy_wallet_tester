from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# hovi's own wallet PIN, introduced in build 34. Before that the wallet had no lock at all and
# `application.pin` was empty for hovi alone — see wallets/hovi/flows/init_flow.py.
#
# Onboarding asks for the same PIN twice, on two screens that differ only in their heading:
#
#     "Enter your PIN"        -> choose it
#     "Enter your PIN Again"  -> confirm it
#
# Matched exactly rather than with contains(), because "Enter your PIN" is a prefix of
# "Enter your PIN Again" and a contains() match would treat the confirm screen as the first one
# and never notice the flow had advanced.
FIRST_ID = (AppiumBy.XPATH, '//*[@text="Enter your PIN"]')
CONFIRM_ID = (AppiumBy.XPATH, '//*[@text="Enter your PIN Again"]')
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Enter your PIN" or @text="Enter your PIN Again"]')


def _digit(d: str):
    """One key of the PIN pad.

    The keys carry their digit as `content-desc`, not as `text`: hovi is React Native, so the
    tappable node is a ViewGroup and the numeral lives on a child. Matching on @text finds the
    child, which is not clickable.
    """
    return (AppiumBy.XPATH, f'//*[@content-desc="{d}"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


def awaiting_confirmation(driver, timeout: float = 2) -> bool:
    return wait_present(driver, CONFIRM_ID, timeout=timeout)


class PinPage(BasePage):
    def enter(self, pin: str):
        """Tap `pin` on the keypad. Does not wait for whatever the screen does next."""
        for digit in pin:
            self.click(_digit(digit))

    def set_pin(self, pin: str):
        """Choose the wallet PIN and confirm it, leaving the confirm screen dismissed.

        Raises if the confirm screen never appears, because that means the first entry was not
        accepted (a PIN of the wrong length is the likely cause) and entering the digits a second
        time would type them into whatever screen is actually in front.
        """
        self.enter(pin)
        if not awaiting_confirmation(self.driver, timeout=self._get_timeout("default")):
            raise RuntimeError(
                f"Hovi did not ask to confirm the PIN after {len(pin)} digits were entered. "
                "The keypad expects a 6-digit PIN — check application.pin for hovi (HOVI_APP_PIN)"
            )
        self.enter(pin)
