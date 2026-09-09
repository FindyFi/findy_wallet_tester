"""Which outcome screens hovi has, for the shared wait loop in base/outcome.py.

These are hovi's own page modules, not copies of anyone else's. A wallet either supplies its own
probe here or says `None` to claim the screen does not exist — see base/screens.py for why
`None` and "not investigated" have to stay different things.
"""
from base.screens import Screens
from wallets.hovi.pages import error_page, loading_page, no_match_page
from wallets.hovi.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="hovi",
    home=_home_on_screen,
    error=error_page.present,
    error_text=error_page.message,
    processing=loading_page.on_screen,
    no_match=no_match_page.present,

    # hovi shows no confirmation after accepting — it goes straight back to home. A claim, not a
    # gap: the terminal wait targets home for this wallet.
    success=None,

    # hovi can flash its home screen while it works on a deeplink, so a glimpse of home inside the
    # first few seconds is not "returned home without offering anything".
    home_grace=5.0,

    # Its spinner buys no extra time: when hovi is still on the processing screen at the timeout,
    # that is the verdict. (Contrast authbound, whose issuance genuinely takes minutes.)
    processing_grace=0.0,

    # hovi's error surface is a banner overlaying whatever else is showing, not a screen, and it is
    # suspected to outlive the case that produced it. See pages/error_page.py — `dismiss()` has
    # never actually fired in a run, so this may be over-engineering for a timed toast.
    error_sticky=True,
)
