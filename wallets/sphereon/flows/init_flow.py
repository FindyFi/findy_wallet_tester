"""Getting sphereon to its home screen: onboard a fresh install, or unlock a returning one.

Onboarding was walked screen by screen on 2026-09-10 against 0.9.0 (build 901) from a cleared
install. The wallet asks for a name and an email address, so unlike every other wallet in the suite
this flow needs more than a PIN — both come from the wallet config (`onboarding.holder_name`,
`onboarding.email`), which reads them from `.env`. They are not sent anywhere: the wallet says so
on both screens ("Your data is only stored locally on your phone") and it self-issues a "Sphereon
Wallet Identity" credential from them, which is why a wiped sphereon comes back holding 1
credential rather than 0.

The sequence, in the order the wallet imposes it:

    Welcome to your Sphereon Wallet          [Get started]
    Getting started (step 1 of 2)            [Next]
      Create wallet 1/3  Select your language        [Continue]   (left at "System default")
      Create wallet 2/3  Enter your name             [Continue]
      Create wallet 3/3  Enter your email address    [Continue]
    You're halfway there (step 2 of 2)       [Next]
      Secure wallet 1/4  Set a 6-digit Sphereon PIN
      Secure wallet 2/4  Repeat your Sphereon PIN
      Secure wallet 3/4  Biometric authentication?   [Maybe later]
      Secure wallet 4/4  Terms & privacy             [Accept Terms & Privacy]
    All done!                                [Start exploring my Wallet]
    Credentials tab

Three things about it are worth not rediscovering:

- **The language step is left alone.** It offers "System default", and the suite already asserts the
  device is in English (`_validate_locale` in the root conftest), so picking a language here would
  be the harness deciding something the device already says.
- **"Continue" is not in the accessibility tree while the keyboard is up.** On the name and email
  screens the button only appears once the IME is dismissed, so each of those steps hides the
  keyboard before looking for it. Without that the step fails with "Continue not found" on a screen
  that visibly has a Continue button on it.
- **Biometrics are declined.** "Maybe later" keeps the wallet on a PIN, which is what this flow can
  answer on any device; enabling it would put a system biometric prompt in front of every unlock.
"""
import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.config import wallet_config
from wallets.sphereon.pages import error_page
from wallets.sphereon.pages.home_page import on_screen as _home_on_screen
from wallets.sphereon.pages.landing_page import LandingPage
from wallets.sphereon.pages.landing_page import SCREEN_ID as _landing_id
from wallets.sphereon.pages.landing_page import on_screen as _landing_on_screen
from wallets.sphereon.pages.pin_page import PinPage
from wallets.sphereon.pages.pin_page import on_screen as _pin_on_screen

logger = logging.getLogger(__name__)

_WALLET = "sphereon"

# Onboarding's own screens. They are walked once, in one place, so their locators live here rather
# than becoming eight page objects nothing else would ever import — the same choice unime made.
_NEXT = (AppiumBy.XPATH, '//*[@content-desc="Next"]')
# Matched on the label's text, not the button's content-desc: the desc changes from step to step
# ("Continue", then "Continue with the creation of your wallet") while the visible word does not.
_CONTINUE = (AppiumBy.XPATH, '//*[@text="Continue"]')
_TEXT_FIELD = (AppiumBy.XPATH, '//android.widget.EditText')

_GETTING_STARTED = (AppiumBy.XPATH, '//*[@text="Getting started"]')
_LANGUAGE_STEP = (AppiumBy.XPATH, '//*[@text="Select your language"]')
_NAME_STEP = (AppiumBy.XPATH, '//*[@text="Enter your name"]')
_EMAIL_STEP = (AppiumBy.XPATH, '//*[@text="Enter your email address"]')
_HALFWAY = (AppiumBy.XPATH, '//*[starts-with(@text,"You")][contains(@text,"halfway")]')
_SET_PIN_STEP = (AppiumBy.XPATH, '//*[@text="Set a 6-digit Sphereon PIN"]')
_REPEAT_PIN_STEP = (AppiumBy.XPATH, '//*[@text="Repeat your Sphereon PIN"]')
_BIOMETRIC_STEP = (AppiumBy.XPATH, '//*[starts-with(@text,"Do you want to enable Biometric")]')
_BIOMETRIC_DECLINE = (AppiumBy.XPATH, '//*[@content-desc="Maybe later"]')
_TERMS_STEP = (AppiumBy.XPATH, '//*[@text="Terms & privacy"]')
_TERMS_ACCEPT = (AppiumBy.XPATH, '//*[@content-desc="Accept Terms & Privacy"]')
_ALL_DONE = (AppiumBy.XPATH, '//*[@text="All done!"]')
_START_EXPLORING = (AppiumBy.XPATH, '//*[@content-desc="Start exploring my Wallet"]')


def _account() -> tuple:
    """(holder name, email) for the wallet's own account, from the config.

    Read here rather than at import: `onboarding.email` is a required `${SPHEREON_EMAIL}`, and a
    module-level read would stop collection for every wallet on a machine that has not set it —
    collection is far wider than execution (`pytest wallets/ -k hovi` still imports this).
    """
    onboarding = wallet_config(_WALLET).get("onboarding", {})
    return onboarding.get("holder_name", ""), onboarding.get("email", "")


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'home', 'pin', 'landing', 'error', or 'unknown'."""
    if _home_on_screen(driver, timeout=timeout):
        return "home"
    if _pin_on_screen(driver, timeout=timeout):
        return "pin"
    if _landing_on_screen(driver, timeout=timeout):
        return "landing"
    if error_page.present(driver, timeout=1):
        return "error"
    return "unknown"


def _back_to_known_state(driver, package: str) -> str:
    """Press back up to 8 times trying to reach a known state, then restart as last resort.

    An error screen is dismissed with its own button instead of being backed out of: a failed case
    parks the wallet there, the button is what the wallet offers to leave it ("Ok, I understand.
    Exit flow"), and it lands straight on home.
    """
    for _ in range(8):
        if driver.current_package != package:
            logger.info("[init_flow] App backgrounded — re-activating")
            driver.activate_app(package)

        state = _detect_state(driver)
        if state == "error":
            logger.info("[init_flow] Error screen from an earlier step — dismissing it")
            error_page.dismiss(driver)
            continue
        if state != "unknown":
            logger.info(f"[init_flow] Reached known state: {state}")
            return state

        driver.back()

    logger.warning("[init_flow] Back presses ineffective — restarting app")
    driver.terminate_app(package)
    driver.activate_app(package)
    state = _detect_state(driver, timeout=5)
    if state != "unknown":
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _hide_keyboard(driver):
    """Dismiss the IME, because the step's Continue button is not in the tree while it is up."""
    try:
        driver.hide_keyboard()
    except Exception:
        pass


def _type_into_field(driver, page, text: str, what: str):
    page.click(_TEXT_FIELD)
    driver.execute_script("mobile: type", {"text": text})
    _hide_keyboard(driver)
    logger.info(f"[init_flow] Entered {what}")


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Walk the whole first-run sequence, from the welcome screen to the Credentials tab."""
    from base.base_page import BasePage

    holder_name, email = _account()
    if not email:
        raise RuntimeError(
            "sphereon onboarding needs an email address: the wallet asks for one and will not "
            "continue without it. Set SPHEREON_EMAIL in .env (see env.example)"
        )
    page = BasePage(driver, **page_args)
    pin_page = PinPage(driver, **page_args)

    def wait(locator, what: str, timeout=None):
        try:
            WebDriverWait(driver, timeout or default_timeout).until(
                EC.presence_of_element_located(locator)
            )
        except TimeoutException:
            raise RuntimeError(f"sphereon onboarding: {what} did not appear")

    logger.info("[init_flow] Welcome screen — getting started")
    LandingPage(driver, **page_args).get_started()

    wait(_GETTING_STARTED, 'the "Getting started" step overview')
    logger.info("[init_flow] Step overview 1 of 2 — creating the wallet")
    page.click(_NEXT)

    wait(_LANGUAGE_STEP, "the language step")
    logger.info('[init_flow] Language step — keeping "System default"')
    page.click(_CONTINUE)

    wait(_NAME_STEP, "the name step")
    _type_into_field(driver, page, holder_name, f"holder name {holder_name!r}")
    page.click(_CONTINUE)

    wait(_EMAIL_STEP, "the email step")
    _type_into_field(driver, page, email, f"email {email!r}")
    page.click(_CONTINUE)

    wait(_HALFWAY, 'the "You\'re halfway there" step overview')
    logger.info("[init_flow] Step overview 2 of 2 — securing the wallet")
    page.click(_NEXT)

    wait(_SET_PIN_STEP, "the PIN step")
    logger.info("[init_flow] PIN step — setting the wallet PIN")
    pin_page.enter_pin(pin)

    wait(_REPEAT_PIN_STEP, "the PIN confirmation step")
    logger.info("[init_flow] PIN confirmation step")
    pin_page.enter_pin(pin)

    wait(_BIOMETRIC_STEP, "the biometrics step")
    logger.info("[init_flow] Biometrics step — declining, the wallet stays on its PIN")
    page.click(_BIOMETRIC_DECLINE)

    wait(_TERMS_STEP, "the terms & privacy step")
    logger.info("[init_flow] Terms & privacy step — accepting")
    page.click(_TERMS_ACCEPT)

    wait(_ALL_DONE, 'the "All done!" screen')
    logger.info("[init_flow] Onboarding finished — entering the wallet")
    page.click(_START_EXPLORING)

    try:
        WebDriverWait(driver, default_timeout).until(lambda d: _home_on_screen(d, timeout=1))
    except TimeoutException:
        raise RuntimeError("Sphereon home screen not reached after onboarding")
    logger.info("[init_flow] Onboarding complete — home screen reached")


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver)
    if state in ("unknown", "error"):
        logger.info("[init_flow] App in intermediate state — returning to a known screen")
        state = _back_to_known_state(driver, package)

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running onboarding")
        _onboard(driver, pin, page_args, default_timeout)

    elif state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    # `and skip_if_done` is load-bearing, not symmetry with the branch above. A reset asks for a
    # wipe, and the wallet is *usually locked* when a session starts — so without it the reset
    # silently became an unlock: measured 2026-09-10, `navigate_to_home` announced "resetting
    # wallet for this session", unlocked instead, and set the session's reset-done flag. The wipe
    # only happened at all because `test_onboarding` asks for one again; a run of the issuance
    # tests alone would have kept whatever the wallet already held while reporting a clean slate.
    elif state == "pin" and skip_if_done:
        logger.info("[init_flow] Lock screen — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)
        try:
            WebDriverWait(driver, default_timeout).until(lambda d: _home_on_screen(d, timeout=1))
        except TimeoutException:
            raise RuntimeError(
                f"PIN unlock failed — check SPHEREON_APP_PIN in .env against the PIN the wallet "
                f"was onboarded with, or set SPHEREON_RESET=true to wipe and onboard again "
                f"({package})"
            )

    else:
        # skip_if_done=False from any state: wipe app data and re-onboard from scratch.
        logger.info(f"[init_flow] reset requested — clearing {package} and re-onboarding")
        driver.execute_script("mobile: clearApp", {"appId": package})
        driver.terminate_app(package)
        driver.activate_app(package)
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.presence_of_element_located(_landing_id)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Landing page not found after reset for {package}.\n"
                "  The app may have crashed or failed to launch after clearing data."
            )
        _onboard(driver, pin, page_args, default_timeout)
