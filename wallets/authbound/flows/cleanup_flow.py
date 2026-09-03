"""Credential cleanup for authbound: the gestures. The loop lives in base/cleanup.py.

    Home dashboard -> tap the front carousel card -> document details ->
    "Delete document" -> "Delete" on the confirm sheet -> back on the Home dashboard.

Two things authbound does not need, both verified live 2026-08-17 and worth keeping written down
because the neighbouring wallets do need them:

- **No authentication step.** authbound deletes on the confirmation alone, so no interstitials are
  declared here. gataca raises the system auth sheet at this point and services it as one.
- **No protected credential.** gataca must preserve its self-attested device credential; authbound
  has no such card, so a target of 0 really does empty the wallet. `can_delete()` is still passed
  so a future undeletable document stops the prune instead of looping on it.
"""
from base import cleanup
from wallets.authbound.pages.credential_detail_page import CredentialDetailPage
from wallets.authbound.pages.home_page import HomePage


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the home screen.

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
        driver, wallet="authbound", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=detail.close,
        delete=detail.delete,
        max_count=max_count, page_args=page_args,
    )
