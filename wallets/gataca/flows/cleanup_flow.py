"""Credential cleanup for Gataca — keeps the wallet from filling up over many test runs.

Issuance tests keep adding credentials; once the wallet is crowded the home credential count
becomes unreliable and the UI sluggish. `prune_credentials` deletes credentials (newest-first as
the list presents them) down to a target count, always preserving the self-attested device
credential. Each delete goes through the wallet's confirm dialog + a system biometric prompt.

Delete flow per credential:
    Home → open a deletable card → Credential details → trash button →
    "Yes, delete" → system biometric prompt (PIN) → back on Home.
"""
import logging

from base.android import authenticate_with_pin
from wallets.gataca.pages.home_page import HomePage
from wallets.gataca.pages.credential_detail_page import CredentialDetailPage

logger = logging.getLogger(__name__)

# Hard cap on delete iterations so a misbehaving delete can never loop forever.
_MAX_DELETIONS = 50


def prune_credentials(driver, max_count: int, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Must start and end on the home screen.

    Returns the number of credentials deleted.
    """
    device_pin = page_args.get("device_pin", "")
    home = HomePage(driver, **page_args)
    home.wait_until_loaded()

    deleted = 0
    for _ in range(_MAX_DELETIONS):
        count = home.count_credentials()
        if count <= max_count:
            break
        if not home.open_deletable_credential():
            logger.info("[cleanup_flow] No deletable credential left — stopping prune")
            break

        detail = CredentialDetailPage(driver, **page_args)
        detail.wait_until_loaded()
        detail.delete()
        # Deletion is confirmed on a system biometric prompt; authenticate via PIN.
        authenticate_with_pin(driver, device_pin)
        home.wait_until_loaded()
        deleted += 1
        logger.info(f"[cleanup_flow] Deleted credential {deleted} (was {count})")

    if deleted:
        logger.info(f"[cleanup_flow] Pruned {deleted} credential(s); now {home.count_credentials()}")
    return deleted
