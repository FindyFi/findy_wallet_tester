"""Which outcome screens unime has, for the shared wait loop in base/outcome.py."""
from base.screens import Screens
from wallets.unime.pages import error_page
from wallets.unime.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="unime",
    home=_home_on_screen,
    error=error_page.present,
    error_text=error_page.message,

    # Nobody has looked for these. Left UNKNOWN rather than claimed absent: unime's error surface
    # was "absent" for months on exactly that reasoning, and it turned out to be in 26 of its own
    # failure dumps. A failure message will say these are unmapped instead of implying they were
    # checked and clean.
    #   processing — no spinner has been seen, but no run has been watched for one either
    #   no_match   — unime has never reached a verification request with an empty wallet
    #   success    — unime returns to home after accepting, but that is untested as a claim

    # unime's error overlay sits on top of home, so home is visible at the same moment. The loop
    # checks error first, which is what keeps a rejection from reading as `dismissed`. The current
    # copy is a toast. In reports/2026-11-20_14-40-17 three attempts dumped a bare home (paradym_verifier
    # 1, procivis_issuer 3, waltid_issuer 2), but logcat shows those were cold starts with no
    # "Dispatching action" and no UserError: unime dropped the link, so `dismissed` was true for
    # them. In every other attempt UserError was logged 0.6-1.5s after delivery, before the wait
    # began, so no grace was needed there. 5s (hovi's value) is kept as cheap insurance against a
    # slower toast; it only delays a genuine `dismissed`.
    home_grace=5.0,
    processing_grace=0.0,
)
