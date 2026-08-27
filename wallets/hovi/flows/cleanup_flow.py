"""Credential cleanup for hovi, so the wallet does not fill up over many test runs.

`prune_credentials` deletes down to a target count, always taking whichever card the home screen
presents first:

    Home -> tap a card (hovi expands it in place, adding Done + a delete icon) ->
    delete icon -> "Delete Credential?" -> Delete -> back on Home.

No authentication, no navigation (cards and count both live on home), and no protected credential,
so a target of 0 really does empty the wallet.
"""
import logging

from base.credential_count import CredentialCountUnavailable
from wallets.hovi.pages.credential_detail_page import CredentialDetailPage, DeleteRefused
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# Hard cap on delete iterations so a misbehaving delete can never loop forever.
_MAX_DELETIONS = 50

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
    home.wait_until_loaded()

    deleted = 0
    restarts = 0
    for _ in range(_MAX_DELETIONS):
        try:
            count = home.count_credentials()
        except CredentialCountUnavailable as e:
            # Without a count there is no way to know when to stop, and deleting blind is worse
            # than leaving the wallet dirty.
            logger.warning(f"[cleanup_flow] Stopping prune, count unavailable: {e}")
            break

        if count <= max_count:
            break

        if not home.open_credential():
            logger.warning(
                f"[cleanup_flow] Wallet reports {count} credential(s) but the home screen presents "
                "no card to open. Stopping prune"
            )
            break

        detail = CredentialDetailPage(driver, **page_args)
        detail.wait_until_loaded()
        if not detail.can_delete():
            logger.info(
                "[cleanup_flow] Credential offers no delete icon. Stopping prune"
            )
            detail.close()
            break

        try:
            detail.delete()
        except DeleteRefused as exc:
            if not app_package:
                raise
            if restarts >= _MAX_RESTARTS:
                raise DeleteRefused(
                    f"{exc} Restarting the app did not help after {_MAX_RESTARTS} attempts."
                ) from exc
            restarts += 1
            logger.warning(
                f"[cleanup_flow] hovi refused the deletion. Restarting the app and retrying "
                f"({restarts}/{_MAX_RESTARTS})"
            )
            driver.terminate_app(app_package)
            driver.activate_app(app_package)
            home.wait_until_loaded()
            continue

        home.wait_until_loaded()
        deleted += 1
        logger.info(f"[cleanup_flow] Deleted credential {deleted} (wallet was at {count})")
    else:
        logger.warning(f"[cleanup_flow] Hit the {_MAX_DELETIONS}-deletion cap. Stopping prune")

    if deleted:
        logger.info(f"[cleanup_flow] Pruned {deleted} credential(s)")
    else:
        logger.info("[cleanup_flow] Nothing to prune")
    return deleted
