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
    # checks error first, which is what keeps a rejection from reading as `dismissed`. No grace is
    # needed on top of that: unime has never been seen to flash home mid-flow.
    home_grace=0.0,
    processing_grace=0.0,
)
