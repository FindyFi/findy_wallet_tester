import logging
import time as _time

from appium.webdriver.common.appiumby import AppiumBy

from base.android import authenticate_with_pin
from base.base_page import BasePage
from base.utils import wait_present

logger = logging.getLogger(__name__)

# The wallet's end-of-transaction screen, shown after authentication succeeds. The SAME screen
# and ids serve both directions — only the header copy differs:
#   issuance     "You have successfully added the following to your wallet"   (2026-08-05)
#   presentation "You successfully shared the following information"          (2026-08-10)
# In both cases there is one `document_success_screen_document_<n>` per document (carrying its
# name, e.g. "Eläkeläistodiste", plus a "View details" link) and a "Close" button that returns
# to the dashboard.
SCREEN_ID = (AppiumBy.ID, "io.authbound.wallet:id/document_success_screen_root")

_HEADER = (AppiumBy.ID,
           "io.authbound.wallet:id/document_success_screen_content_header_description")

# One container per document added by this offer; index starts at 0. The container itself
# carries no text — the document's name is a TextView inside it, alongside a "View details"
# link that has to be filtered out.
_DOCUMENT_TEXT = (AppiumBy.XPATH,
                  '//*[starts-with(@resource-id, '
                  '"io.authbound.wallet:id/document_success_screen_document_")]'
                  '//android.widget.TextView')
_NOT_A_NAME = frozenset({"View details"})

# The "Close" label carries no id of its own — the button container does.
_CLOSE = (AppiumBy.ID, "io.authbound.wallet:id/document_success_screen_button")


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


# The wallet's busy indicator ("Please wait…"). While it is up the transaction is still in
# flight, so the outcome wait extends rather than expiring — a presentation to a remote verifier
# has been seen to outlast the 30s credential_offer timeout (2026-08-10).
_LOADING = (AppiumBy.XPATH, '//*[starts-with(@text, "Please wait")]')


def wait_for_outcome(driver, error_locator, timeout: float = 30, device_pin: str = "",
                     max_extension: float = 180, max_prompts: int = 12) -> str:
    """Poll for the post-authentication result.

    Returns 'success', 'error', 'prompt_loop' or 'timeout'.

    Shared by the issuance and verification flows — both end on this screen when they work and
    on the wallet's error screen when they don't. Waiting for only one of the two would report
    the other as a missing screen.

    Answers up to `max_prompts` *repeat* authentication prompts while waiting: a presentation
    asks for device authentication more than once (observed 2026-08-10), and a prompt left
    sitting unanswered is indistinguishable from a success screen that never arrived.

    The wallet demands one authentication **per document it presents** — measured live
    2026-08-12: six requested documents needed seven authentications before the presentation
    completed. The count therefore grows with the wallet's contents, which is why sharing used
    to finish after a single prompt and later appeared to loop forever. Callers should pass
    `max_prompts` from the request screen's document count; the default is generous enough to
    cover a moderately full wallet. Past the cap this returns 'prompt_loop' rather than
    answering indefinitely.

    Extends the deadline by up to `max_extension` seconds while the wallet shows its "Please
    wait…" indicator, so a slow round trip to a remote verifier isn't reported as a missing
    result. The extension is bounded, so a wallet stuck on that screen still fails.
    """
    end = _time.time() + timeout
    extended = 0.0
    answered = 0
    while _time.time() < end:
        if wait_present(driver, SCREEN_ID, timeout=1):
            return "success"
        if wait_present(driver, error_locator, timeout=1):
            return "error"
        if extended < max_extension and wait_present(driver, _LOADING, timeout=1):
            if extended == 0:
                logger.info("[document_success] Wallet is busy ('Please wait…') — extending the wait")
            end += 5
            extended += 5
            continue
        if device_pin and answered < max_prompts:
            try:
                if authenticate_with_pin(driver, device_pin, detect_timeout=1):
                    answered += 1
                    logger.info(
                        f"[document_success] Repeat authentication prompt {answered}/{max_prompts}"
                        " — answered with PIN"
                    )
                    # Answering is progress, so buy time rather than racing the original
                    # deadline: each document costs a prompt and several seconds.
                    if extended < max_extension:
                        end += 10
                        extended += 10
            except Exception as e:
                # Speculative: a prompt that half-appears, or one that closes on its own mid-way,
                # must not abort the wait for the real outcome.
                logger.warning(f"[document_success] Repeat PIN attempt failed, still waiting: {e}")

    if answered >= max_prompts:
        return "prompt_loop"
    return "timeout"


class DocumentSuccessPage(BasePage):
    def added_document_names(self) -> list:
        """Names of the documents this offer added, for logging what actually arrived."""
        names = []
        for label in self.driver.find_elements(*_DOCUMENT_TEXT):
            text = (label.get_attribute("text") or "").strip()
            if text and text not in _NOT_A_NAME:
                names.append(text)
        return names

    def close(self):
        self.click(_CLOSE)
