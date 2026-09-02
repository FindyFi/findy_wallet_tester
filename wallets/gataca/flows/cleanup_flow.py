"""Credential cleanup for Gataca: the gestures. The loop lives in base/cleanup.py.

    Home -> open a deletable card -> Credential details -> trash button ->
    "Yes, delete" -> system biometric prompt (PIN) -> back on Home.

Gataca always keeps its self-attested device credential, so `open_deletable_credential()` does the
filtering that other wallets do with a `can_delete()` gate on the detail screen.
"""
from base import cleanup, interstitials
from wallets.gataca.pages.credential_detail_page import CredentialDetailPage
from wallets.gataca.pages.home_page import HomePage

# Deleting is confirmed on the system auth sheet. Declaring it as an interstitial rather than
# calling authenticate_with_pin inline means the prune loop services it wherever it appears, which
# matters because gataca does not always raise it at the same point.
_INTERSTITIALS = (interstitials.device_pin_prompt(),)


def prune_credentials(driver, max_count: int, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Must start and end on the home screen.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)

    def open_detail() -> bool:
        if not home.open_deletable_credential():
            return False
        detail.wait_until_loaded()
        return True

    return cleanup.prune_credentials(
        driver, wallet="gataca", home=home,
        open_detail=open_detail,
        delete=detail.delete,
        max_count=max_count, interstitials=_INTERSTITIALS, page_args=page_args,
    )
