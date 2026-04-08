import time
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# Both setup and confirmation screens share the same resource-id; title text differs
INIT_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="PinCodeInitializationScreen"]')
CONFIRM_TITLE_ID = (AppiumBy.XPATH,
    '//*[@resource-id="PinCodeInitializationScreen.title" and @text="Confirm PIN code"]')

LOGIN_SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="PinCodeCheckScreen"]')

# Keypad prefix differs per screen — pass the correct prefix to enter_pin()
_INIT_PREFIX = "PinCodeInitializationScreen.keypad"
_CHECK_PREFIX = "PinCodeCheckScreen.keypad"

# Android BiometricPrompt dialog shown before the PIN screen
_BIOMETRIC_DIALOG_ID = (AppiumBy.XPATH,
    '//*[@resource-id="ch.procivis.one.wallet.trial:id/fingerprint_container"]')
_USE_PIN_BUTTON = (AppiumBy.XPATH,
    '//*[@resource-id="ch.procivis.one.wallet.trial:id/cancel_button"]')


def on_screen(driver, timeout=2) -> bool:
    """Return True if the login PIN check screen is currently shown."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(LOGIN_SCREEN_ID))
        return True
    except TimeoutException:
        return False


def biometric_on_screen(driver, timeout=2) -> bool:
    """Return True if the Android biometric (fingerprint) prompt is showing."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(_BIOMETRIC_DIALOG_ID)
        )
        return True
    except TimeoutException:
        return False


def _digit_locator(prefix: str, digit: str):
    return (AppiumBy.XPATH, f'//*[@resource-id="{prefix}.{digit}"]')


class PinPage(BasePage):
    def enter_pin(self, pin: str, keypad_prefix: str = _INIT_PREFIX):
        """Tap each digit of pin on the custom keypad."""
        delay = self.timeouts.get("pin_digit_delay", 0.3)
        for digit in pin:
            self.click(_digit_locator(keypad_prefix, digit))
            if delay:
                time.sleep(delay)

    def enter_login_pin(self, pin: str):
        """Tap each digit of pin on the login (check) keypad."""
        self.enter_pin(pin, keypad_prefix=_CHECK_PREFIX)

    def dismiss_biometric_prompt(self):
        """Tap 'USE PIN CODE' to dismiss the Android biometric dialog."""
        self.click(_USE_PIN_BUTTON)
