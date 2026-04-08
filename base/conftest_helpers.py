"""Shared helpers for wallet conftest fixtures.

These utilities extract the boilerplate that every wallet's ``_ensure_home``
fixture needs: navigating to the home screen (with optional session reset),
capturing failure artifacts, and running teardown.  Import them in each
wallet conftest to avoid repeating the same ~40 lines.
"""
import logging

from base.utils import sanitize_test_name

logger = logging.getLogger(__name__)


def navigate_to_home(app, request, init_flow):
    """Navigate to the wallet home screen, handling session reset if configured.

    Checks ``onboarding.skip_if_done`` in the wallet config:
    - If False and the session hasn't been reset yet, performs a full reset
      (wipes app data, re-onboards) once per pytest session.
    - Otherwise navigates to home from whatever state the app is in.
    """
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]
    skip_if_done = app.config.get("onboarding", {}).get("skip_if_done", True)

    if not skip_if_done and not getattr(request.config, "_session_reset_done", False):
        logger.info("[conftest] skip_if_done=false — resetting wallet for this session")
        init_flow.run(
            app.driver,
            pin=pin,
            app_package=app_package,
            skip_if_done=False,
            **app.page_args,
        )
        request.config._session_reset_done = True
    else:
        init_flow.run(
            app.driver,
            pin=pin,
            app_package=app_package,
            skip_if_done=True,
            **app.page_args,
        )


def capture_failure_artifact(app, request):
    """Save a screenshot or XML dump when a test has failed.

    Respects the wallet's ``reporting`` config:
    - ``xml_on_failure: true``  → saves page XML
    - ``screenshot_on_failure: true`` (default) → saves screenshot

    Sets ``request.node._artifact_captured`` so the root conftest's ``app``
    fixture doesn't attempt a second capture.
    """
    failed = hasattr(request.node, "rep_call") and request.node.rep_call.failed
    if not failed or getattr(request.node, "_artifact_captured", False):
        return

    reporting = app.config.get("reporting", {})
    test_name = sanitize_test_name(request.node.name)

    if reporting.get("xml_on_failure", False):
        try:
            xml_dir = request.config._run_dir / "xml_dumps"
            xml_dir.mkdir(parents=True, exist_ok=True)
            path = xml_dir / f"{test_name}.xml"
            path.write_text(app.driver.page_source, encoding="utf-8")
            logger.info(f"[conftest] XML dump saved: {path}")
            request.node._artifact_captured = True
        except Exception as e:
            logger.warning(f"[conftest] Could not save XML dump: {e}")

    if reporting.get("screenshot_on_failure", True):
        try:
            screenshot_dir = request.config._run_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = screenshot_dir / f"{test_name}.png"
            app.driver.save_screenshot(str(path))
            logger.info(f"[conftest] Screenshot saved: {path}")
            request.node._artifact_captured = True
        except Exception as e:
            logger.warning(f"[conftest] Could not save screenshot: {e}")


def teardown_test(app, request, init_flow):
    """Capture any failure artifact then navigate back to the home screen.

    Call this at the end of an ``_ensure_home`` fixture (after ``yield``) as the
    standard teardown for wallets that don't need extra post-test steps.
    """
    capture_failure_artifact(app, request)

    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]
    try:
        init_flow.run(
            app.driver,
            pin=pin,
            app_package=app_package,
            skip_if_done=True,
            **app.page_args,
        )
    except Exception as e:
        logger.warning(f"[conftest] Could not return to home after test: {e}")
