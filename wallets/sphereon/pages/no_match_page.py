"""The wallet answering, inside a presentation request, that it holds nothing that satisfies it.

Captured live 2026-09-10 on 0.9.0 (build 901) against `hovi_verifier`, with the wallet holding only
its own self-issued identity credential:

    The following information will be shared
    No Available Credentials   [Show values]   [0 available]

This is not a separate screen. It renders *within* the information request, so the heading and the
refusal are in the tree together — exactly hovi's `no_match` shape, and the reason a flow must look
for it after reaching the request screen rather than instead of it.

Kept distinct from `nothing_to_present`, which the test raises for an empty wallet: this one is a
disagreement between what the verifier asked for and what the wallet holds, which is a finding
about the request, not about issuance having failed earlier in the run.

Both lines are required. "No Available Credentials" is the label the wallet writes when a requested
credential has no candidate, and the "<n> available" chip carries the count; matching either alone
would fire on a partially satisfiable request, where some inputs do have candidates.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

_LABEL = (AppiumBy.XPATH, '//*[@text="No Available Credentials"]')
_ZERO_AVAILABLE = (AppiumBy.XPATH, '//*[@content-desc="0 available" or @text="0 available"]')

SCREEN_ID = _LABEL


def present(driver, timeout: float = 1) -> bool:
    if not wait_present(driver, _LABEL, timeout=timeout):
        return False
    return wait_present(driver, _ZERO_AVAILABLE, timeout=0.5)


def message(driver, timeout: float = 1) -> str:
    """The wallet's own words for the refusal, for quoting in the failure."""
    if not present(driver, timeout=timeout):
        return ""
    return "No Available Credentials (0 available)"
