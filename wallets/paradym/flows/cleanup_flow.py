"""Credential cleanup for paradym: the gestures. The loop lives in base/cleanup.py.

    Home -> "All cards" -> Cards list -> row arrow -> Card details ->
    header archive icon -> "Yes, archive" -> back to Home.

Two things that make paradym different from the wallets already ported:

- **Archive is the delete.** paradym has no other removal action; the sheet says the card will be
  deleted from the wallet, and the wallet's own total drops when it completes.
- **A delete does not land on home.** It returns to the Cards list, so each iteration walks back
  before the loop re-reads the count. That is what `return_to_home()` is for.

No authentication, and no protected card.
"""
import logging

from base import cleanup
from wallets.paradym.pages import credential_detail_page
from wallets.paradym.pages.credential_detail_page import CredentialDetailPage
from wallets.paradym.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# The tap that opens a card is not always taken. Measured 2026-09-03: the first archive works, and
# the next iteration taps a row arrow that is present and clickable but lands while the list is
# still animating in, so nothing opens and the app simply stays on the Cards list. Retrying from
# home clears it. Bounded, so a card that genuinely will not open fails the prune instead of
# spinning.
_MAX_OPEN_ATTEMPTS = 3


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the home screen.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)

    def open_detail() -> bool:
        for attempt in range(1, _MAX_OPEN_ATTEMPTS + 1):
            if not home.open_credential():
                return False
            if credential_detail_page.on_screen(driver, timeout=detail._get_timeout("default")):
                return True
            logger.warning(
                f"[cleanup_flow] paradym did not open the card on attempt "
                f"{attempt}/{_MAX_OPEN_ATTEMPTS} — returning home and retrying"
            )
            home.return_to_home()
        detail.wait_until_loaded()   # raises with paradym's own message
        return True

    def delete() -> None:
        detail.archive()
        home.return_to_home()

    return cleanup.prune_credentials(
        driver, wallet="paradym", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=home.return_to_home,
        delete=delete,
        max_count=max_count, page_args=page_args,
    )
