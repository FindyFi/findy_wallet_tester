"""procivis publishes Code/Message/Cause behind a "?" — these hold the path that reaches it open.

The evidence behind this file is in the XML dumps every failed procivis test already writes. In the
2026-09-21 run four of five issuance failures reported only "did not complete within timeout", and
their dumps show `InvitationProcessScreen.animation.warning` with `InvitationProcessScreen.header.info`
sitting right there unopened. The gate only recognised `.animation.error`, so the collector below it
was unreachable for the *common* failure.

These tests drive the real issuance path from the process screen — the page's wait, then
flows/_failures.py's label and details — against a fake driver, so they fail if the gate narrows
again, if the details screen stops being opened, or if one missing button goes back to discarding
the whole collection.
"""
import pytest

from _procivis_fakes import FakeDriver, details_screen, in_front, process_screen
from base import outcome, progress
from base.conftest_helpers import _error_surface, _read_error_text
from base.flow_context import FlowContext
from wallets.procivis.flows import credential_flow


def _issue_from_the_process_screen(driver):
    """The real `_await_process`: page wait, then label, then details — and the verdict."""
    ctx = FlowContext(flow="credential_flow", wallet="procivis", what="credential_issuance",
                      page_args={"timeouts": {"credential_offer": 2, "default": 1}, "debug": False})
    credential_flow._await_process(driver, ctx, when="in this test")


@pytest.fixture(autouse=True)
def fresh():
    progress.reset()
    yield
    progress.reset()


@pytest.fixture
def failing_driver():
    def build(animation, *, label="Connection failed", expandable=True):
        return FakeDriver(
            process_screen(animation, label),
            on_tap={"InvitationProcessScreen.header.info": details_screen(expandable=expandable)},
        )
    return build


# --- the gap this file exists for ----------------------------------------------------------------

def test_a_warning_animation_opens_the_details_screen(failing_driver):
    """The common failure. Before this, it fell through to the timeout and published nothing."""
    driver = failing_driver("warning")

    with pytest.raises(outcome.FlowFailure) as excinfo:
        _issue_from_the_process_screen(driver)

    assert "InvitationProcessScreen.header.info" in driver.tapped, \
        "the '?' was never tapped, so the details screen was never opened"
    assert "BR_0395" in str(excinfo.value)
    assert "Remote HTTP request status failure (4xx)" in str(excinfo.value)
    # The wording the timeout branch publishes; kept in step with it so this is not vacuous.
    assert "did not complete within the timeout" not in str(excinfo.value)


def test_an_error_animation_still_opens_the_details_screen(failing_driver):
    """The variant that already worked — widening the gate must not have cost it."""
    driver = failing_driver("error")

    with pytest.raises(outcome.FlowFailure) as excinfo:
        _issue_from_the_process_screen(driver)

    assert "InvitationProcessScreen.header.info" in driver.tapped
    assert "BR_0395" in str(excinfo.value)


def test_the_issuance_failed_label_still_counts_as_a_failure(failing_driver):
    """CredentialAcceptProcessScreen has only ever shown `.warning` plus this label."""
    driver = failing_driver("warning", label="Issuance failed")

    with pytest.raises(outcome.FlowFailure) as excinfo:
        _issue_from_the_process_screen(driver)

    assert "BR_0395" in str(excinfo.value)


def test_a_missing_expand_button_does_not_discard_the_collected_details(failing_driver):
    """Not every error carries an expandable cause; Code and Message are on screen regardless."""
    driver = failing_driver("warning", expandable=False)

    with pytest.raises(outcome.FlowFailure) as excinfo:
        _issue_from_the_process_screen(driver)

    assert "details could not be read" not in str(excinfo.value)
    assert "BR_0395" in str(excinfo.value)


def test_the_failure_leaves_the_details_screen_up_for_teardowns_screenshot(failing_driver):
    """The user's call: the evidence is procivis's Code/Message/Cause, so no Back after reading
    them. Teardown's init_flow closes the details screen afterwards."""
    driver = failing_driver("warning")

    with pytest.raises(outcome.FlowFailure):
        _issue_from_the_process_screen(driver)

    assert driver.went_back == 0
    assert in_front(driver, "InvitationErrorDetailsScreen")


def test_no_retry_survives_so_a_read_failure_is_not_retried(failing_driver):
    """`[no_retry]` is what keeps a decided failure from being rerun — see commit a28d0b5."""
    driver = failing_driver("warning")

    with pytest.raises(outcome.FlowFailure) as excinfo:
        _issue_from_the_process_screen(driver)

    assert str(excinfo.value).endswith(" [no_retry]")


# --- what app.log publishes --------------------------------------------------------------------
#
# The digest reads the wallet's copy through base/screens.py. Before wallets/procivis/screens.py
# existed, `_error_surface` reported "unmapped" and every procivis block in the 2026-09-21 run said
# `wallet said: (unknown — no error screen is mapped for procivis)` — with the copy sitting in the
# XML dump named on the very next line.

def test_procivis_no_longer_reports_an_unmapped_error_surface():
    assert _error_surface("procivis") == "mapped"


@pytest.mark.parametrize("animation,label", [
    ("warning", "Connection failed"),
    ("error", "No credential interaction found."),
    ("warning", "Issuance failed"),
])
def test_the_digest_reads_the_copy_procivis_actually_shows(animation, label):
    """All three strings procivis has ever printed on a process screen, across 75 dumps."""
    driver = FakeDriver(process_screen(animation, label))
    assert _read_error_text(driver, "procivis") == label


def test_a_processing_screen_is_not_published_as_an_error():
    """The process screen is also what procivis shows while it works — only the animation decides."""
    driver = FakeDriver([
        {"resource-id": "InvitationProcessScreen"},
        {"resource-id": "InvitationProcessScreen.label", "text": "Please wait"},
    ])
    assert _read_error_text(driver, "procivis") == ""


def test_the_details_screen_is_not_read_as_an_error_either():
    """Teardown may re-read the copy with the details screen left open: nothing there is a label."""
    assert _read_error_text(FakeDriver(details_screen()), "procivis") == ""


def test_reading_the_copy_never_taps_anything():
    """Teardown must not drive the UI of a screen a test just failed on."""
    driver = FakeDriver(process_screen("warning"))
    _read_error_text(driver, "procivis")
    assert driver.tapped == []
    assert driver.went_back == 0
