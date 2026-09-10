import logging
import time as _time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.android import handle_permission_if_present, authenticate_with_pin
from base.utils import wait_present
from wallets.hovi.pages.landing_page import LandingPage, SCREEN_ID as _landing_id
from wallets.hovi.pages.landing_page import on_screen as _landing_on_screen
from wallets.hovi.pages.home_page import SCREEN_ID as _home_id
from wallets.hovi.pages.home_page import on_screen as _home_on_screen
from wallets.hovi.pages import pin_page
from wallets.hovi.pages.pin_page import PinPage

logger = logging.getLogger(__name__)

_secret_key_screen = ("xpath", '//*[@text="Secure Your Secret Key"]')
_acknowledge_checkbox = ("xpath", '//*[@text="I have copied and stored my secret key securely"]')
_access_wallet_btn = ("xpath", '//*[@text="Access My Wallet"]')


def _unlock_if_prompted(driver, device_pin: str, timeout: float = 3) -> bool:
    """Answer hovi's lock sheet if it is up. False when there was nothing to answer.

    hovi locks itself from build 34 on and asks again on every activation — including the
    re-activation inside `_back_to_known_state`, which is why this is called from the recovery
    loop as well as once at the start, rather than only on entry.
    """
    if not device_pin:
        return False
    if authenticate_with_pin(driver, device_pin, detect_timeout=timeout):
        logger.info("[init_flow] Answered hovi's lock screen with the device PIN")
        return True
    return False


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'landing', 'home', or 'unknown'."""
    if _home_on_screen(driver, timeout=timeout):
        return "home"
    if _landing_on_screen(driver, timeout=timeout):
        return "landing"
    return "unknown"


def _back_to_known_state(driver, package: str, device_pin: str = "") -> str:
    """Press back up to 8 times trying to reach a known state, then restart as last resort.

    Never press Back at the lock sheet: Back cancels the authentication instead of answering it,
    so a locked hovi would burn all 8 presses and then restart into the same sheet.
    """
    for _ in range(8):
        if driver.current_package != package:
            logger.info("[init_flow] App backgrounded — re-activating")
            driver.activate_app(package)

        _unlock_if_prompted(driver, device_pin, timeout=2)
        state = _detect_state(driver)
        if state != "unknown":
            logger.info(f"[init_flow] Reached known state: {state}")
            return state

        driver.back()

    logger.warning("[init_flow] Back presses ineffective — restarting app")
    driver.terminate_app(package)
    driver.activate_app(package)
    _unlock_if_prompted(driver, device_pin, timeout=5)
    state = _detect_state(driver, timeout=5)
    if state != "unknown":
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Complete the full onboarding flow from the landing screen to home.

    Sequence (Hovi Wallet):
      1. Landing  → tap "Create New Wallet"
      2. Secret key backup screen → tap checkbox → tap "Access My Wallet"
      3. Notification permission dialog (system) → handled by handle_permission_if_present
      4. Wallet PIN → chosen, then confirmed on a second screen
      5. Home screen reached.

    Recovery is by secret key, not by PIN: the key is shown at step 2 and the PIN only locks this
    installation. Build 34 added the PIN; before it hovi had no lock at all, which is why hovi's
    `application.pin` was the one empty pin in the fleet.
    """
    # Step 1 — Landing
    LandingPage(driver, **page_args).get_started()

    # Step 2 — Secret key backup
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_secret_key_screen)
    )
    logger.info("[init_flow] Secret key screen — acknowledging and continuing")
    driver.find_element(*_acknowledge_checkbox).click()
    driver.find_element(*_access_wallet_btn).click()

    # Step 3 — Notification permission (system dialog)
    #
    # Waited for, not sampled. The default 0.5s detect window is a poll for a dialog that might
    # already be up; here hovi has just been told to open the wallet and *then* asks Android for
    # the notification permission, so the dialog is always a beat behind this line. Sampling
    # returned False every time on a freshly wiped app, and the step-4 wait for home then sat
    # behind a dialog nothing had dismissed — onboarding could never finish after a wipe.
    if not handle_permission_if_present(driver, detect_timeout=default_timeout):
        logger.info("[init_flow] No notification permission dialog — already granted")

    # Step 4 — Wallet PIN (build 34 and later)
    #
    # Guarded rather than assumed, so this same flow still completes on an older build that goes
    # straight from the permission dialog to home.
    if pin_page.on_screen(driver, timeout=default_timeout):
        if not pin:
            raise RuntimeError(
                "Hovi is asking for a wallet PIN but none is configured. Set HOVI_APP_PIN in "
                ".env — hovi had no lock before build 34, so its pin was empty by default"
            )
        logger.info("[init_flow] Choosing the wallet PIN")
        PinPage(driver, **page_args).set_pin(pin)
    else:
        logger.info("[init_flow] No PIN screen — this build does not lock the wallet")

    # Step 5 — Wait for home, and wait for it to stay
    #
    # Presence once is not enough here. hovi mounts its home screen and re-renders it a beat
    # later, so `SCREEN_ID` can match and then vanish; a caller that counts credentials
    # immediately got `CredentialCountUnavailable: home screen is not showing` on a freshly
    # onboarded wallet. Only the wipe path sees this — an already-onboarded run returns early
    # from `run()` long before here — which is exactly the path a clean-slate run takes.
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise RuntimeError("Hovi home screen not reached after onboarding")

    deadline = _time.monotonic() + default_timeout
    while _time.monotonic() < deadline:
        _time.sleep(1.0)
        if _home_on_screen(driver, timeout=default_timeout):
            break
    else:
        raise RuntimeError(
            "Hovi home screen appeared after onboarding but did not stay — it kept "
            "disappearing between checks, so no page object can rely on it"
        )
    logger.info("[init_flow] Onboarding complete — home screen reached")


def _wipe_and_onboard(driver, package: str, pin: str, page_args: dict, default_timeout: float):
    """Erase all app data and walk onboarding from the landing screen.

    `mobile: clearApp` is an adb-level operation: it needs no UI, no unlock and no known screen,
    which is what makes it the one recovery that works on an app that cannot be driven at all.
    """
    logger.info(f"[init_flow] skip_if_done=false — clearing {package} and re-onboarding")
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


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    # A reset wipes first and never probes state at all.
    #
    # There is nothing worth detecting in a wallet that is about to be erased, and probing first
    # was actively harmful: the wipe used to sit in the `else` of `if landing / elif home`, so it
    # was reachable only *after* reaching a known screen — precisely what a broken wallet cannot
    # do. On 2026-09-09 hovi was crash-looping on every launch (a credential persisted from a
    # rejected paradym offer kills `CredentialCard` on the home render), `_detect_state` returned
    # "unknown", `_back_to_known_state` exhausted its back-presses and raised, and all 10 hovi
    # cells errored in fixture setup — with `HOVI_RESET=true` set, on the one situation the reset
    # exists for. `mobile: clearApp` would have fixed it in two seconds.
    if not skip_if_done:
        _wipe_and_onboard(driver, package, pin, page_args, default_timeout)
        return

    # hovi locks itself from build 34 on, and asks on every activation. The sheet is the system
    # BiometricPrompt: "Authenticate to proceed", fingerprint, and a "Use PIN" fallback that opens
    # `com.android.systemui:id/lockPassword` — the *device* credential, not the 6-digit PIN chosen
    # during onboarding. So it is answered with the device PIN, like gataca's.
    #
    # Answered before `_detect_state`, because the sheet hides both known screens: detection would
    # return "unknown" and `_back_to_known_state` would press Back, which cancels authentication.
    #
    # The window is `default_timeout`, not a token 2-3s: conftest activates the app and the sheet
    # takes a beat to surface, and a probe that lands in that gap sends the whole run down the
    # back-press path. Fingerprint injection is not an option — `mobile: fingerprint` works only on
    # an emulator (base.android.fingerprint_emulation_available) and hovi runs on the phone.
    device_pin = page_args.get("device_pin", "")
    _unlock_if_prompted(driver, device_pin, timeout=default_timeout)

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package, device_pin)

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running onboarding")
        _onboard(driver, pin, page_args, default_timeout)
    else:
        # `_detect_state` returns only landing/home/unknown, and unknown either resolves to one of
        # the two above or raises — so reaching here means home.
        logger.info("[init_flow] Already on home screen — skipping")
