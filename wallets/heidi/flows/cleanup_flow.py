"""Credential cleanup for heidi: the gestures. The loop lives in base/cleanup.py.

    Dashboard -> "Credentials" tile -> list -> card -> detail ->
    header's right-hand button -> "DELETE" -> back to the dashboard.

Heidi is the only wallet here where **both** counting and opening are navigation steps, and where
neither the delete button nor the back button carries any label — see
`pages/credential_detail_page` for what that forced. No authentication, no protected credential.
"""
from base import cleanup
from wallets.heidi.pages.credential_detail_page import CredentialDetailPage
from wallets.heidi.pages.home_page import HomePage


def prune_credentials(driver, max_count: int = 0, **page_args) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the dashboard.

    Returns the number of credentials deleted.
    """
    home = HomePage(driver, **page_args)
    detail = CredentialDetailPage(driver, **page_args)

    def open_detail() -> bool:
        if not home.open_credential():
            home.return_to_dashboard()
            return False
        detail.wait_until_loaded()
        return True

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
