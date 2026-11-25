import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from base import outcome
from base.base_page import BasePage
from base.outcome import FlowFailure
from base.utils import wait_present

logger = logging.getLogger(__name__)

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestAcceptProcessScreen"]')
_success = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestAcceptProcessScreen.animation.success"]')


class ProofRequestAcceptProcessPage(BasePage):
    def wait_for_result(self) -> bool:
        """Wait for proof request sharing to complete. True once procivis's success animation shows.

        False when the screen had already gone before anything was seen: no evidence either way,
                so the flow looks at what is on screen instead.
        No success animation: fails as `processing`, never `rejected` — no failure surface on this
                screen has ever been captured, so silence after Share is a timeout, not a verdict.
        A WebDriverException before the animation propagates (the flow makes it `lost_session`);
        after it, it is logged and ignored — the share already succeeded, by procivis's own word.
        """
        t = self._get_timeout("credential_offer")
        if not wait_present(self.driver, SCREEN_ID, timeout=1):
            logger.info("[proof_request_accept] Share process screen had already closed")
            return False

        try:
            WebDriverWait(self.driver, t).until(EC.presence_of_element_located(_success))
        except TimeoutException:
            raise FlowFailure(outcome.PROCESSING, (
                f"[verification_flow] Verification sharing did not complete — procivis's success "
                f"animation never appeared within {t:.0f}s after Share. No failure screen is "
                "mapped for procivis's share step, so a refusal would look the same as a wallet "
                "still working: this is a timeout, not a verdict"
            ))

        # Screen auto-closes after ~5s; wait for it to disappear
        try:
            WebDriverWait(self.driver, t).until(EC.invisibility_of_element_located(SCREEN_ID))
        except TimeoutException:
            pass  # already gone or took longer — not a failure
        except WebDriverException as exc:
            logger.warning(f"[proof_request_accept] Session trouble after the success animation, "
                           f"ignored — the share had already succeeded: {exc}")
        return True
