"""Credential cleanup for authbound — keeps the wallet from filling up over many test runs.

Every passing issuance adds a credential and nothing removed them, so the wallet grew run over run
(2 → 3 → 4 between 2026-08-13 and 2026-08-17). That inflates the counts the issuance assertion
compares and changes what verifiers have to match against, so state stops being comparable between
runs. `prune_credentials` deletes credentials down to a target count.

Delete flow per credential:
    Home dashboard -> tap the front carousel card -> document details ->
    "Delete document" -> "Delete" on the confirm sheet -> back on the Home dashboard.

Two differences from gataca's equivalent, both verified live 2026-08-17:

- **No authentication step.** authbound deletes on the confirmation alone; there is no biometric
  or PIN prompt to answer, so nothing here calls `authenticate_with_pin`.
- **No protected credential.** gataca must preserve its self-attested device credential; authbound
  has no such card, so a target of 0 really does empty the wallet. `can_delete()` is still checked
  per credential so a future undeletable document stops the prune instead of looping on it.
"""
import logging

from base.credential_count import CredentialCountUnavailable
from wallets.authbound.pages.credential_detail_page import CredentialDetailPage
from wallets.authbound.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# Hard cap on delete iterations so a misbehaving delete can never loop forever.
_MAX_DELETIONS = 50


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the home screen.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    home.wait_until_loaded()

    deleted = 0
    for _ in range(_MAX_DELETIONS):
        try:
            count = home.count_credentials()
        except CredentialCountUnavailable as e:
            # Without a count there is no way to know when to stop, and deleting blind is worse
            # than leaving the wallet dirty.
            logger.warning(f"[cleanup_flow] Stopping prune — count unavailable: {e}")
            break

        if count <= max_count:
            break

        if not home.open_credential():
            logger.warning(
                f"[cleanup_flow] Wallet reports {count} credential(s) but the dashboard presents "
                "no card to open — stopping prune"
            )
            break

        detail = CredentialDetailPage(driver, **page_args)
        detail.wait_until_loaded()
        if not detail.can_delete():
            logger.info(
                "[cleanup_flow] Front credential offers no delete button — stopping prune"
            )
            detail.close()
            break

        detail.delete()
        home.wait_until_loaded()
        deleted += 1
        logger.info(f"[cleanup_flow] Deleted credential {deleted} (wallet was at {count})")
    else:
        logger.warning(
            f"[cleanup_flow] Hit the {_MAX_DELETIONS}-deletion cap — stopping prune"
        )

    if deleted:
        logger.info(f"[cleanup_flow] Pruned {deleted} credential(s)")
    else:
        logger.info("[cleanup_flow] Nothing to prune")
    return deleted
