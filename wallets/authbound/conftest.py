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
    "  Set requires_fingerprint to false in wallets/authbound/config.json to skip this check."
)


@pytest.fixture(autouse=True)
def _require_fingerprint(app, request):
    """Fail fast when the device has no fingerprint enrolled — it can't be automated.

    Checked once per session (one adb call), before any credential flow gets far enough to
    walk into Android's enrollment wizard. Skipped entirely unless the wallet's config asks
    for it via `requires_fingerprint`, and skipped when the check can't tell (no adb, or a
    device without a fingerprint sensor) so it never blocks on a guess.

    The config key is `requires_fingerprint`, not `device_setup.*`: nothing in this suite ever
    *sets up* a fingerprint. Enrollment needs a real finger on the sensor (or, on an emulator,
    `adb emu finger touch 1` answering the enrollment wizard by hand — see the README), and
    `mobile: fingerprint` only simulates a touch on a print that is already enrolled. This
    fixture asserts a precondition and nothing more.

    A precondition only. This used to also re-read the state after every test and again at
    session end, because enrollments were seen disappearing mid-run — that turned out to be
    Appium's default `locksettings` unlock strategy running `locksettings clear --old <pin>`,
    which wipes every enrolled fingerprint. The root conftest pins `unlockStrategy` to
    `uiautomator` (see the driver capabilities there), so nothing in the suite clears the lock
    credential any more and the monitoring was only logging a warning on healthy runs.
    """
    if not app.config.get("requires_fingerprint", False):
        return
    if getattr(request.config, "_fingerprint_checked", None) is None:
        serial = app.device_serial()
        request.config._fingerprint_checked = fingerprint_enrolled(serial)
        if request.config._fingerprint_checked is None:
            logger.warning(
                "[fingerprint] Could not read enrollment state — continuing"
            )
        elif not request.config._fingerprint_checked:
            request.config._fingerprint_reason = _NO_FINGERPRINT.format(
                device=serial or "the device")

    if request.config._fingerprint_checked is False:
        pytest.fail(request.config._fingerprint_reason, pytrace=False)


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
