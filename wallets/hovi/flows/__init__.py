"""Shared helpers for hovi's flows."""
import logging

from wallets.hovi.pages import error_page

logger = logging.getLogger(__name__)


def clear_stale_error(driver, app_package: str, flow: str) -> bool:
    """Clear a leftover error banner before firing a deeplink. Returns True if one is still up.

    hovi's banner is believed to survive until the app restarts, so one left by an earlier case
    would make this one look rejected. Clearing it first means a banner seen afterwards is evidence
    about *this* case; only if it refuses to go does the error check get disabled, and the caller
    passes the result through as `error_before`.

    Worth re-checking on a device: `error_page.present()` has been False at the start of every case
    ever recorded, including reruns seconds after one ended with the banner up, which looks more
    like an ordinary timed toast than something that persists.
    """
    if not error_page.present(driver, timeout=0.5):
        return False
    if error_page.dismiss(driver, app_package):
        logger.info(f"[{flow}] Cleared a stale hovi error banner before the deeplink")
        return False
    logger.warning(
        f"[{flow}] A hovi error banner is stuck on screen before this deeplink, "
        "so it cannot be used to diagnose this case"
    )
    return True
