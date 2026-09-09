"""Deleting credentials down to a target count — the loop, shared; the gestures, per wallet.

Three wallets had written this independently (hovi, gataca, authbound) and agreed on every
structural decision: read the count, stop if it is low enough, open whatever card the list presents
first, delete it, re-read the count, and cap the iterations so a delete that silently fails cannot
spin forever. What differs is only the gestures — which card is openable, whether deleting needs
authentication, what a refusal looks like.

That split is why this is worth sharing: the five wallets with no cleanup at all need the loop and
its bounds, not another copy of someone else's locators. See base/screens.py for the same argument
about outcome screens.

The two bounds are the part most easily got wrong, so they are stated once here:

- `max_deletions` stops a delete that reports success while leaving the card in place. Without it
  the loop is infinite, because the count never falls.
- An unreadable count **stops** the prune rather than deleting blind. There is no way to know when
  to stop without a count, and leaving a wallet dirty is better than deleting from a wallet whose
  contents we cannot see.
"""
import logging
from typing import Callable, Optional

from base import interstitials as _interstitials
from base.credential_count import CredentialCountUnavailable
from base.flow_context import FlowContext

logger = logging.getLogger(__name__)

_MAX_DELETIONS = 50


class DeleteRefused(Exception):
    """The wallet accepted the delete confirmation and then kept the credential.

    Distinct from "there is no delete button": the wallet said yes and did not do it. hovi does
    this intermittently and recovers on an app restart, which is why callers get a chance to retry
    rather than the prune simply failing.
    """


def prune_credentials(driver, *, wallet: str, home, open_detail: Callable[[], bool],
                      delete: Callable[[], None], can_delete: Optional[Callable[[], bool]] = None,
                      close_detail: Optional[Callable[[], None]] = None,
                      on_refused: Optional[Callable[[], bool]] = None,
                      max_count: int = 0, interstitials=(), page_args: Optional[dict] = None,
                      max_deletions: int = _MAX_DELETIONS) -> int:
    """Delete credentials until at most `max_count` remain. Starts and ends on the home screen.

    The wallet supplies the gestures:

    - `home`          its HomePage, for `wait_until_loaded()` and `count_credentials()`
    - `open_detail()` open the next deletable credential; False when there is none left, which is
                      a normal end state for a wallet with undeletable credentials
    - `delete()`      delete the open credential; may raise `DeleteRefused`
    - `can_delete()`  optional gate for wallets that protect some credentials (authbound's and
                      gataca's self-attested device credential)
    - `close_detail()` optional way back to home when a credential turns out to be undeletable
    - `on_refused()`  optional recovery from `DeleteRefused`; return True to retry, and keep your
                      own attempt budget — hovi restarts the app here

    `interstitials` are serviced after each delete, which is how a wallet whose delete is gated by
    a biometric or PIN prompt (gataca) gets that handled without this loop knowing about it.

    Returns the number of credentials deleted.
    """
    ctx = FlowContext(flow="cleanup_flow", wallet=wallet, what="cleanup",
                      page_args=page_args or {},
                      device_pin=(page_args or {}).get("device_pin", ""))
    home.wait_until_loaded()

    deleted = 0
    for _ in range(max_deletions):
        try:
            count = home.count_credentials()
        except CredentialCountUnavailable as exc:
            logger.warning(f"[cleanup_flow] Stopping prune, count unavailable: {exc}")
            break

        if count <= max_count:
            break

        if not open_detail():
            # Not an anomaly: a wallet may hold credentials it will not let anyone delete —
            # gataca's self-attested device credential is the standing case, and it is exactly
            # why the count can sit above max_count with nothing left to do.
            logger.info(
                f"[cleanup_flow] No deletable credential left in {wallet} ({count} remain, "
                f"target is at most {max_count}). Stopping prune"
            )
            break

        if can_delete is not None and not can_delete():
            logger.info("[cleanup_flow] Credential cannot be deleted. Stopping prune")
            if close_detail is not None:
                close_detail()
            break

        try:
            delete()
        except DeleteRefused as exc:
            if on_refused is None or not on_refused():
                raise
            logger.warning(f"[cleanup_flow] {wallet} refused the deletion, retrying: {exc}")
            home.wait_until_loaded()
            continue

        # A delete may be gated by a system prompt; service it before believing the count again.
        if interstitials:
            _interstitials.service(driver, ctx, interstitials, ctx.fired)

        home.wait_until_loaded()
        deleted += 1
        logger.info(f"[cleanup_flow] Deleted credential {deleted} ({wallet} was at {count})")
    else:
        logger.warning(
            f"[cleanup_flow] Hit the {max_deletions}-deletion cap. Stopping prune — a delete is "
            "probably reporting success without removing the credential"
        )

    logger.info(f"[cleanup_flow] Pruned {deleted} credential(s)" if deleted
                else "[cleanup_flow] Nothing to prune")
    return deleted
