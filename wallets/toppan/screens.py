"""Which outcome screens toppan has, for the shared failure diagnosis in base/outcome.py.

toppan's own probes, each anchored on a captured dump (the page modules say which). Not a copy of
any other wallet's probes — base/screens.py says why a copied probe is worse than none.

toppan's flows keep their own waits; they do not call `outcome.wait_for`. What this declaration
buys is the rest of the shared machinery: the device-backed `unroutable` diagnosis, a failure
message that says which surfaces were actually looked for, and the app.log digest quoting toppan's
own error copy.
"""
from functools import partial

from base.config import wallet_config
from base.screens import Screens
from wallets.toppan.pages import error_dialog, result_page
from wallets.toppan.pages.home_page import on_screen as _home_on_screen

# The error probe is anchored on toppan's package, read from the same config.json the flows get
# their `app_package` from.
PACKAGE = wallet_config("toppan")["application"]["package"]

SCREENS = Screens(
    name="toppan",
    home=_home_on_screen,

    # The AlertDialog with toppan's message and a single OK, anchored on toppan's package.
    error=partial(error_dialog.present, package=PACKAGE),
    error_text=partial(error_dialog.message, package=PACKAGE),

    # "Request docType … is not available" on the result screen, in place of the request.
    no_match=result_page.no_match,

    # Left UNKNOWN deliberately, not claimed absent. No dump shows either: the "Adding your
    # credential" overlay and the "Information Shared" screen are only ever on screen in tests
    # that pass, and a passing test saves no dump. Both are matched by the flows on their text.
    # processing: UNKNOWN
    # success: UNKNOWN
)
