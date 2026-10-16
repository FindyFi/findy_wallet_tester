"""Put procivis in the background before a deeplink is fired at it.

Why, as far as it is known. Fired while procivis is in front, the deeplink's `am start` pauses and
resumes its activity within ~15 ms (onHostPause → onNewIntent → onHostResume in logcat). Reading
procivis's own source (app/hooks/pin-code/pin-code-check.ts, app/hooks/navigation/deep-link.ts),
that pause looks to procivis like going to the background: it pushes its PinCodeCheck cover and
goBack()s on resume while the deeplink handler pushes the "Connecting..." sheet. That race is
SUSPECTED as the cause of the 1.87.1 freezes on hovi_verifier and procivis_verifier (2026-10-09,
"Unable to find viewState", nothing readable on screen). It has not been reproduced, and a 1.87.1
nightly run without backgrounding also passed.

Kept because it is cheap (at most 3 s), it follows the path a person's tapped link takes and hovi's
own HOME press, and it makes a wallet that never came forward (`absent`) distinguishable from one
that came forward to its home screen (`dismissed`).
"""
import logging
import time

from selenium.common.exceptions import WebDriverException

from base import progress

logger = logging.getLogger(__name__)

KEYCODE_HOME = 3
APP_STATE_FOREGROUND = 4          # appium's ApplicationState.RUNNING_IN_FOREGROUND
TIMEOUT = 3.0
POLL = 0.2


def background(driver, app_package: str, flow: str) -> None:
    """Press HOME and wait, bounded, until Android reports procivis out of the foreground.

    Never fails the test on a driver error: the deeplink is fired afterwards whatever happened
    here, and the trail records which case it was, as a step.
    """
    logger.info(f"[{flow}] Backgrounding procivis before the deeplink")
    try:
        driver.press_keycode(KEYCODE_HOME)
    except WebDriverException as exc:
        logger.warning(f"[{flow}] Could not press HOME ({exc}) — firing anyway")
        progress.step("could not background procivis before the deeplink")
        return
    deadline = time.monotonic() + TIMEOUT
    while True:
        try:
            state = driver.query_app_state(app_package)
        except WebDriverException as exc:
            logger.warning(f"[{flow}] Could not read procivis's app state ({exc}) — firing anyway")
            progress.step("pressed HOME before the deeplink; procivis's app state was unreadable")
            return
        if state != APP_STATE_FOREGROUND:
            progress.step("backgrounded procivis before the deeplink")
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    logger.warning(f"[{flow}] procivis still reported foreground {TIMEOUT:.0f}s after HOME — "
                   "firing anyway")
    progress.step("pressed HOME before the deeplink; procivis still reported foreground")
