"""The one thing credential counting shares across wallets: how it reports failure.

*How* a wallet is counted is per-wallet code and lives in that wallet's `HomePage` — the
mechanisms genuinely differ (cards on the home screen, a count label on a separate screen,
occurrences in the page source), and pretending otherwise only hides the differences.

What every wallet's `count_credentials()` does agree on:

1. **Return a real number, or raise `CredentialCountUnavailable` — never a silent 0.**
   Every implementation used to end in `except Exception: return 0`, which makes "the wallet
   is empty" and "my locator broke" the same answer. A broken locator then reads as a failed
   issuance, and in the wallets that only log the delta it reads as success.
2. **Verify the screen before believing a count.** An empty accessibility tree on the wrong
   screen counts as zero just as convincingly as an empty wallet does.
3. **Leave the app on the screen it was found on**, so callers can count at any point in a
   flow without it becoming a navigation step.
"""
import logging

logger = logging.getLogger(__name__)


class CredentialCountUnavailable(Exception):
    """A wallet could not read how many credentials it holds.

    Deliberately distinct from a count of 0: "I looked, the wallet is empty" is a fact about
    the wallet, while "the list never opened" or "the card locator matched nothing on an
    unknown screen" is a broken test. Only the first one is a number.

    Callers should treat this as "count unknown" — warn and skip count-based checks — rather
    than as an issuance failure, since it says nothing about whether the credential arrived.
    """


def read(home, *, when: str):
    """`home.count_credentials()`, or None when the wallet cannot report one.

    An unreadable count says nothing about whether the credential arrived, so it must not fail an
    issuance test — but it must not pass silently either. Returns None and warns; `assert_increased`
    then records the absence of evidence.
    """
    try:
        return home.count_credentials()
    except CredentialCountUnavailable as exc:
        logger.warning(f"[test] Credential count {when} is unavailable: {exc}")
        return None


def assert_increased(before, after, *, request, wallet: str, issuer: str, case: str) -> None:
    """Assert the wallet holds one more credential than it did. The shared end of every issuance test.

    Before this existed the eight wallets disagreed about what issuance even means: authbound, hovi,
    toppan and unime asserted the count moved; gataca, heidi and paradym read both counts and only
    logged the delta, so an issuance that stored nothing passed; procivis could not count at all.
    Three wallets were publishing green cells that no check stood behind.

    A readable count is always asserted. An unreadable one warns and records
    `("count_evidence", "unavailable")` on the test, so a pass with no evidence stays
    distinguishable from a pass with evidence rather than both rendering as the same green cell.
    """
    if before is None or after is None:
        request.node.user_properties.append(("count_evidence", "unavailable"))
        logger.warning(
            f"[test] '{case}' from '{issuer}': the flow reported success but {wallet} could not "
            "report a count — this run has NO evidence the credential was stored"
        )
        return

    logger.info(f"[test] Credential '{case}' from '{issuer}' issued to wallet "
                f"({wallet}: {before} → {after}, +{after - before})")
    assert after > before, (
        f"[not_stored] Credential '{case}' from '{issuer}' did not reach {wallet}: the flow "
        f"completed without error but the count stayed at {after}"
    )

