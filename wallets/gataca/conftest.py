import logging
import time

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from wallets.gataca.flows import init_flow
from wallets.gataca.flows import setup_flow

logger = logging.getLogger(__name__)


def _resolve_did_method(app, request):
    """The DID method this test wants.

    If the test is parametrized with a `did_method` (the credential matrix), use that value.
    Otherwise fall back to the config's "did_method" (a string, or the first of a list).
    """
    callspec = getattr(request.node, "callspec", None)
    if callspec and "did_method" in callspec.params:
        return callspec.params["did_method"]
    configured = app.config.get("did_method", setup_flow.DEFAULT_DID_METHOD)
    return configured[0] if isinstance(configured, list) else configured


def pytest_collection_modifyitems(items):
    """Order gataca tests: setup tests first, credential tests grouped by did_method, reset last.

    Switching the active DID is slow (Settings → select alias → PIN → home), so every test for one
    method should run consecutively. The credential-reset test (@pytest.mark.gataca_cleanup) is
    forced to the very end so it wipes credentials only after everything has run. Stable sort keeps
    relative order within each group.
    """
    def _order_key(item):
        if item.get_closest_marker("gataca_cleanup"):
            return (2, "")  # reset runs last
        callspec = getattr(item, "callspec", None)
        method = callspec.params.get("did_method", "") if callspec else ""
        return (1, method) if method else (0, "")  # install/onboarding first, then by DID method

    items.sort(key=_order_key)


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the wallet is on the home screen before and after every test.

    Tests that need a specific DID active (rather than the post-onboarding default did:gatc:)
    should mark themselves with @pytest.mark.gataca_did. Which DID method(s) to use comes from the
    wallet config's "did_method" key — a single value ("jwk") or a list (["jwk","gatc","ebsi"]) to
    run the credential matrix across methods (see setup_flow.DID_METHODS). The active DID is only
    switched when the method actually changes (tracked in request.config._active_did_method), so
    grouped tests don't re-switch needlessly.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    navigate_to_home(app, request, init_flow)

    if request.node.get_closest_marker("gataca_did"):
        # The app is closed between tests (see conftest._clear_recent_apps), and Gataca reverts to
        # its default DID on a cold start — so re-establish the configured DID for EVERY test
        # (no caching). ensure_did short-circuits if it's already active, and selects an existing
        # identity rather than creating a duplicate when the profile already exists.
        did_method = _resolve_did_method(app, request)
        try:
            already_active = setup_flow.ensure_did(
                app.driver,
                app_package=app.config["application"]["package"],
                did_method=did_method,
                **app.page_args,
            )
            if not already_active:
                logger.info(f"[conftest] DID '{did_method}' activated — waiting for wallet to sync")
                time.sleep(8)
            navigate_to_home(app, request, init_flow)
        except Exception as e:
            logger.warning(f"[conftest] Could not enable DID '{did_method}': {e}")

    yield
    teardown_test(app, request, init_flow)
