"""Credential cleanup for unime: the gestures. The loop lives in base/cleanup.py.

    Home -> tap a card -> Credential Information -> kebab menu -> "Delete credential" ->
    confirm -> back on Home.

No authentication (unlike gataca, which raises the system auth sheet, and toppan, which gates the
same detail screen behind a device-auth prompt), and no protected credential, so a target of 0
really does empty the wallet.
"""
from base import cleanup
from wallets.unime.pages.credential_detail_page import CredentialDetailPage
from wallets.unime.pages.home_page import HomePage


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
        driver, wallet="unime", home=home,
        open_detail=open_detail,
        can_delete=detail.can_delete,
        close_detail=detail.close,
        delete=detail.delete,
        max_count=max_count, page_args=page_args,
    )
