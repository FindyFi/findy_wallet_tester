"""Credential cleanup for Gataca: the gestures. The loop lives in base/cleanup.py.

    Home -> open a deletable card -> Credential details -> trash button ->
    "Yes, delete" -> system biometric prompt (PIN) -> back on Home.

Gataca always keeps its self-attested device credential, so `open_credential()` does the
filtering that other wallets do with a `can_delete()` gate on the detail screen. Credentials are
listed per active DID, so a caller prunes one DID at a time (see tests/test_cleanup.py).
"""
import logging

from base import cleanup, interstitials
from base.credential_count import CredentialCountUnavailable
from wallets.gataca.pages.credential_detail_page import CredentialDetailPage
from wallets.gataca.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# Deleting is confirmed on the system auth sheet. Declaring it as an interstitial rather than
# calling authenticate_device inline means the prune loop services it wherever it appears, which
# matters because gataca does not always raise it at the same point.
_INTERSTITIALS = (interstitials.device_auth_prompt(),)


class _PruneHome:
    """The home screen as `base.cleanup` counts it.

    base/cleanup.py stops pruning when a count is unavailable, because it cannot tell when to stop
    without one. gataca's count scrolls the whole list and raises only when that cannot be done;
    stopping the prune there would leave the wallet dirty for the next run. The cards on screen are
    a floor of the real count and the top of the list is always laid out, so when scrolling fails
    this falls back to them: zero visible cards means an empty list, and anything else keeps the
    prune going until `open_detail` runs out of deletable cards.
    """

    def __init__(self, home: HomePage):
        self._home = home

    def wait_until_loaded(self):
        self._home.wait_until_loaded()

    def count_credentials(self) -> int:
        try:
            return self._home.count_credentials()
        except CredentialCountUnavailable as exc:
            visible = self._home.visible_cards()
            logger.warning(f"[cleanup_flow] Could not scroll-count the credentials ({exc}); "
                           f"going by the {visible} on screen")
            return visible


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Must start and end on the home screen.

    The list shows only the active DID's credentials, so the caller makes the DID active first
    (tests/test_cleanup.py does, per configured method).

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)

    def open_detail() -> bool:
        if not home.open_credential():
            return False
        detail.wait_until_loaded()
        return True

    return cleanup.prune_credentials(
        driver, wallet="gataca", home=_PruneHome(home),
        open_detail=open_detail,
        delete=detail.delete,
        max_count=max_count, interstitials=_INTERSTITIALS, page_args=page_args,
    )
