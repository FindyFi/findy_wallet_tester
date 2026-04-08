import logging

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from base.utils import sanitize_test_name
from wallets.paradym.flows import init_flow, settings_flow
from wallets.paradym.pages.home_page import HomePage

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the Paradym wallet is on the home screen before every test.

    Runs automatically for all tests in wallets/paradym/tests/.
    Handles any app state left by a previous test: PIN screen, intermediate
    screens, credential offer screens, etc. — always lands on home.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    navigate_to_home(app, request, init_flow)

    # Enable Developer Mode once per session (requires home screen to be visible).
    if not getattr(request.config, "_dev_mode_enabled", False):
        try:
            settings_flow.enable_dev_mode(app.driver, **app.page_args)
            logger.info("[conftest] Developer mode enabled for this session")
        except Exception as e:
            logger.warning(f"[conftest] Could not enable developer mode: {e}")
        request.config._dev_mode_enabled = True

        # Return to home after navigating to Settings.
        init_flow.run(
            app.driver,
            pin=pin,
            app_package=app_package,
            skip_if_done=True,
            **app.page_args,
        )

    yield  # Test runs here

    # Navigate back to home and capture failure screenshot (common teardown).
    teardown_test(app, request, init_flow)

    # Paradym-specific: collect in-app debug logs when a test fails.
    # Wait for home to be fully rendered before opening the navigation drawer.
    failed = hasattr(request.node, "rep_call") and request.node.rep_call.failed
    if failed:
        try:
            HomePage(app.driver, **app.page_args).wait_until_loaded()
            log_text = settings_flow.collect_debug_logs(app.driver, **app.page_args)
            if log_text:
                test_name = sanitize_test_name(request.node.name)
                log_file = request.config._run_dir / f"app_logs_{test_name}.json"
                log_file.write_text(log_text, encoding="utf-8")
                logger.info(f"[conftest] Saved debug logs to {log_file}")
        except Exception as e:
            logger.warning(f"[conftest] Could not collect debug logs on failure: {e}")
