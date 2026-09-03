"""Credential cleanup for procivis: the gestures. The loop lives in base/cleanup.py.

    Wallet -> card's ".card.header.openDetail" -> CredentialDetailScreen -> kebab ->
    "Delete credential" -> CredentialDeletePromptScreen -> hold the button 3 s -> back on Wallet.

No authentication and no protected credential. The one thing that is unlike every other wallet
here: the confirmation is a **press-and-hold**, not a tap.
"""
from base import cleanup
from wallets.procivis.pages.credential_detail_page import CredentialDetailPage
from wallets.procivis.pages.home_page import HomePage


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the wallet screen.

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
        driver, wallet="procivis", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=detail.close,
        delete=detail.delete,
        max_count=max_count, page_args=page_args,
    )
