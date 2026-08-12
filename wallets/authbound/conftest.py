import logging

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from base.utils import fingerprint_enrolled
from wallets.authbound.flows import init_flow

logger = logging.getLogger(__name__)

_NO_FINGERPRINT = (
    "authbound needs a fingerprint enrolled on {device} and there is none.\n"
    "  Storing a credential requires a biometric-backed key. With nothing enrolled, Android\n"
    "  opens its enrollment wizard instead of an authentication prompt, and enrolling needs a\n"
    "  real finger on the sensor — no test can get past it.\n"
    "  Enroll one by hand (Settings > Security > Fingerprint), then confirm with:\n"
    "      adb -s {device} shell dumpsys fingerprint    # \"count\":0 means none\n"
    "  Note: changing the device lock PIN wipes existing enrollments.\n"
    "  Set device_setup.fingerprint to false in wallets/authbound/config.json to skip this check."
)


def pytest_sessionfinish(session, exitstatus):  # exitstatus required by the hookspec
    """Log the fingerprint enrollment state once more, after every driver has quit.

    Brackets the whole session together with the per-test check below. If enrollment is present
    on the last test but gone here, whatever removes it happens in teardown (driver.quit, app
    close, recents clearing) rather than during a test — which is the one window the per-test
    check can't see.
    """
    serial = getattr(session.config, "_fingerprint_serial", None)
    if serial is None:
        return
    final = fingerprint_enrolled(serial)
    logger.warning(
        f"[device_setup] Fingerprint enrollment at session end: {final} "
        f"(was {getattr(session.config, '_fingerprint_checked', None)} at the last test)"
    )


@pytest.fixture(autouse=True)
def _require_fingerprint(app, request):
    """Fail fast when the device has no fingerprint enrolled — it can't be automated.

    Checked once per session (one adb call), before any credential flow gets far enough to
    walk into Android's enrollment wizard. Skipped entirely unless the wallet's config asks
    for it via `device_setup.fingerprint`, and skipped when the check can't tell (no adb, or
    a device without a fingerprint sensor) so it never blocks on a guess.
    """
    if not app.config.get("device_setup", {}).get("fingerprint", False):
        yield
        return
    if getattr(request.config, "_fingerprint_checked", None) is None:
        serial = app.device_serial()
        request.config._fingerprint_serial = serial
        request.config._fingerprint_checked = fingerprint_enrolled(serial)
        if request.config._fingerprint_checked is None:
            logger.warning(
                "[device_setup] Could not read fingerprint enrollment state — continuing"
            )
        elif not request.config._fingerprint_checked:
            request.config._fingerprint_reason = _NO_FINGERPRINT.format(
                device=serial or "the device")

    if request.config._fingerprint_checked is False:
        pytest.fail(request.config._fingerprint_reason, pytrace=False)

    yield

    # The enrollment has been observed vanishing mid-session, and nothing in this suite can
    # delete one (the only biometric call is `mobile: fingerprint`, an emulator-only
    # simulation). Re-read it after each test and log the transition, so a run pinpoints which
    # test it disappears on instead of leaving us to guess.
    still = fingerprint_enrolled(app.device_serial())
    if still is not request.config._fingerprint_checked:
        logger.warning(
            f"[device_setup] Fingerprint enrollment changed during "
            f"{request.node.name}: {request.config._fingerprint_checked} -> {still}"
        )
        request.config._fingerprint_checked = still


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the wallet is on the home screen before and after every test.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    navigate_to_home(app, request, init_flow)
    yield
    teardown_test(app, request, init_flow)
