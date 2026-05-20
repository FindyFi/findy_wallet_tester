import logging

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)

# Android HOME key — used to background the app before firing a deeplink.
_KEYCODE_HOME = 3


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a presentation request deeplink and share credentials in the wallet.

    TODO: implement this flow by mapping the screens that appear after the deeplink.
    Investigation pipeline (reference_dev_tools.md):
      1. python .../appium.py keyevent 3                    # HOME — background the app
      2. python .../appium.py deeplink "<url>" "<package>"  # fire the deeplink
      3. python .../appium.py screen                        # check what appeared
      4. python .../appium.py tap "text:Share"              # drive through the flow
      5. Repeat step 3–4 for each screen until verification is complete

    Typical steps to implement:
      - Handle a PIN / biometric prompt if the app locks on deeplink
      - Handle a verifier consent / request review screen
      - Confirm sharing the requested credentials
      - Handle any post-flow navigation back to home
    """
    url = provider.get(credential_name)

    logger.info("[verification_flow] Backgrounding app before deeplink")
    driver.press_keycode(_KEYCODE_HOME)

    logger.info(f"[verification_flow] Opening deeplink for '{credential_name}'")
    driver.execute_script("mobile: deepLink", {"url": url, "package": app_package})

    raise NotImplementedError(
        "example verification_flow.run() is not yet implemented — "
        "use the investigation pipeline to map screens and build the flow"
    )
