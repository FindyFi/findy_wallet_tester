"""Credential cleanup for heidi: the gestures. The loop lives in base/cleanup.py.

    Dashboard -> "Credentials" tile -> list -> card -> detail ->
    header's right-hand button -> "DELETE" -> back to the dashboard.

Heidi is the only wallet here where **both** counting and opening are navigation steps, and where
neither the delete button nor the back button carries any label — see
`pages/credential_detail_page` for what that forced. No authentication, no protected credential.
"""
import logging

from base import cleanup
from wallets.heidi.pages.credential_detail_page import CredentialDetailPage
from wallets.heidi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# How many times to re-open a card whose tap did not land.
#
# heidi's credential list re-renders right after it loads — every iteration of a prune logs
# `went stale before the tap ... re-locating` from base_page.click, which re-locates and taps
# again. That handles the *stale element*; it does not handle the tap arriving while the list is
# still settling, where the tap is simply swallowed and the detail never opens. Measured
# 2026-09-07: a prune from 10 credentials deleted 7 and then failed with 3 left, and one from 7
# deleted 6 — so this is not "the last card", it is roughly a per-iteration chance.
#
# `open_credential()` cannot tell the difference on its own: it taps and returns True without
# checking that anything opened. Rather than lengthen a sleep, the open is verified and retried.
_OPEN_ATTEMPTS = 3


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the dashboard.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)

    def open_detail() -> bool:
        for attempt in range(1, _OPEN_ATTEMPTS + 1):
            if not home.open_credential():
                home.return_to_dashboard()
                return False
            try:
                detail.wait_until_loaded()
                return True
            except RuntimeError:
                if attempt == _OPEN_ATTEMPTS:
                    raise
                logger.warning(
                    f"[cleanup_flow] heidi: the card tap did not open the detail "
                    f"(attempt {attempt}/{_OPEN_ATTEMPTS}) — the list re-renders under the tap. "
                    "Returning to the dashboard and retrying"
                )
                home.return_to_dashboard()
                home.wait_until_loaded()
        return False

    def delete() -> None:
        detail.delete()
        home.return_to_dashboard()

    def close_detail() -> None:
        home.return_to_dashboard()

    return cleanup.prune_credentials(
        driver, wallet="heidi", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=close_detail,
        delete=delete,
        max_count=max_count, page_args=page_args,
    )
