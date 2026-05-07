import logging
import time

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from wallets.gataca.flows import init_flow
from wallets.gataca.flows import setup_flow

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the wallet is on the home screen before and after every test.

    Tests that need the EBSI Subject DID active (rather than the post-onboarding
    default did:gatc:) should mark themselves with @pytest.mark.gataca_ebsi.
    Gataca's own issuer/verifier accept did:gatc:; only cross-wallet interop
    (e.g. Paradym verifier rejecting did:gatc:) requires the EBSI switch, which
    additionally needs an email credential bound to did:key: — a manual OTP step.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    navigate_to_home(app, request, init_flow)

    if request.node.get_closest_marker("gataca_ebsi") and not getattr(request.config, "_ebsi_setup_done", False):
        try:
            already_active = setup_flow.ensure_ebsi_did(
                app.driver,
                app_package=app.config["application"]["package"],
                **app.page_args,
            )
            if not already_active:
                logger.info("[conftest] EBSI DID enabled — waiting for wallet to sync new profile")
                time.sleep(8)
            request.config._ebsi_setup_done = True
            navigate_to_home(app, request, init_flow)
        except Exception as e:
            logger.warning(f"[conftest] Could not enable EBSI DID: {e}")
            request.config._ebsi_setup_done = True

    yield
    teardown_test(app, request, init_flow)
