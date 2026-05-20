import logging

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)

# Android HOME key — used to background the app before firing a deeplink.
# Many wallets only process incoming deeplinks when they are in the background.
_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the wallet.

    TODO: implement this flow by mapping the screens that appear after the deeplink.
    Investigation pipeline (reference_dev_tools.md):
      1. python .../appium.py keyevent 3                    # HOME — background the app
      2. python .../appium.py deeplink "<url>" "<package>"  # fire the deeplink
      3. python .../appium.py screen                        # check what appeared
      4. python .../appium.py tap "text:Accept"             # drive through the flow
      5. Repeat step 3–4 for each screen until credential is stored

    Typical steps to implement:
      - Handle a PIN / biometric prompt if the app locks on deeplink
      - Handle an "Untrusted Connection" or issuer consent screen (if present)
      - Wait for the credential offer screen and accept it
      - Verify the app returns to home or shows a success state
    """
    url = provider.get(credential_name)

    logger.info("[credential_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    raise NotImplementedError(
        "example credential_flow.run() is not yet implemented — "
        "use the investigation pipeline to map screens and build the flow"
    )
