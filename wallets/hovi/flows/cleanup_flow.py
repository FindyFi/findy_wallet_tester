"""Credential cleanup for hovi: the gestures. The loop lives in base/cleanup.py.

    Home -> tap a card (hovi expands it in place, adding Done + a delete icon) ->
    delete icon -> "Delete Credential?" -> Delete -> back on Home.

No authentication, no navigation (cards and count both live on home), and no protected credential,
so a target of 0 really does empty the wallet.
"""
import logging

from base import cleanup
from wallets.hovi.pages.credential_detail_page import CredentialDetailPage
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# hovi sometimes accepts the confirmation and then reports "Failed to delete credential", leaving
# the card in place. A restart clears it, so a refusal is retried. Bounded, so a credential that
# simply cannot be deleted stops the prune instead of cycling forever.
_MAX_RESTARTS = 3


def prune_credentials(driver, max_count: int = 0, app_package: str = "", **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the home screen.

    `app_package` enables the restart-and-retry path for hovi's intermittent delete refusal; without
    it a refusal is fatal, since there is nothing to restart.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)
    restarts = {"n": 0}

    def open_detail() -> bool:
        if not home.open_credential():
            return False
        detail.wait_until_loaded()
        return True

    def on_refused() -> bool:
        if not app_package or restarts["n"] >= _MAX_RESTARTS:
            return False
        restarts["n"] += 1
        logger.warning(
            f"[cleanup_flow] Restarting hovi after a refused deletion "
            f"({restarts['n']}/{_MAX_RESTARTS})"
        )
        driver.terminate_app(app_package)
        driver.activate_app(app_package)
        return True

    return cleanup.prune_credentials(
        driver, wallet="hovi", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=detail.close,
        delete=detail.delete,
        on_refused=on_refused,
        max_count=max_count, page_args=page_args,
    )
