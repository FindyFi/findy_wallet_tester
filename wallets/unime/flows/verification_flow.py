import logging

from base.android import handle_permission_if_present
from base.utils import wait_present
from providers.base import DeeplinkProvider
from wallets.unime.pages import pin_page as pin_screen
from wallets.unime.pages.pin_page import PinPage
from wallets.unime.pages.home_page import SCREEN_ID as _home_id

logger = logging.getLogger(__name__)

_KEYCODE_HOME = 3


def _send_deeplink(driver, url: str, package: str):
    """Send a deeplink via adb am start, targeting the component directly.

    See credential_flow.py for the rationale (bypass app-chooser dialog).
    """
    driver.execute_script("mobile: shell", {
        "command": "am",
        "args": ["start", "-a", "android.intent.action.VIEW",
                 "-d", url,
                 "-n", f"{package}/.MainActivity"],
    })


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the UniMe wallet.

    NOTE: Verification via deeplink has not been confirmed to work with UniMe.
    The app does not show a verification consent screen when receiving openid4vp://
    deeplinks — it returns silently to the home screen.  This flow fires the deeplink
    and waits; if no consent screen appears it raises NotImplementedError so the test
    is marked as an expected failure rather than silently passing.

    TODO: Once UniMe verification deeplinks are confirmed to work (with any verifier),
    replace the raise below with the actual consent / share steps.
    """
    url = provider.get(credential_name)
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    logger.info("[verification_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    _send_deeplink(driver, url, app_package)

    handle_permission_if_present(driver)

    if pin_screen.on_screen(driver, timeout=3):
        if not pin:
            raise RuntimeError(
                "App showed password screen after deeplink — pass pin= to verification_flow.run()"
            )
        logger.info("[verification_flow] Password screen appeared — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)

    # Check if the app returned to home without showing a consent screen.
    if wait_present(driver, _home_id, timeout=5):
        raise NotImplementedError(
            "[verification_flow] UniMe returned to home after verification deeplink "
            "without showing a consent screen.  Deeplink-based verification is not yet "
            "confirmed to work with this wallet.  "
            "TODO: investigate which verifiers and URI schemes UniMe supports."
        )
