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


class CredentialCountUnavailable(Exception):
    """A wallet could not read how many credentials it holds.

    Deliberately distinct from a count of 0: "I looked, the wallet is empty" is a fact about
    the wallet, while "the list never opened" or "the card locator matched nothing on an
    unknown screen" is a broken test. Only the first one is a number.

    Callers should treat this as "count unknown" — warn and skip count-based checks — rather
    than as an issuance failure, since it says nothing about whether the credential arrived.
    """
