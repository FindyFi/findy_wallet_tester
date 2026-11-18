"""Every way procivis's flows can end, mapped to the outcome it raises and how far it got.

procivis kept its own waits — one WebDriverWait for the offer or its process screen, one for the
offer to close, a fixed wait for the proof request, and the two process pages' own waits. What
changed is what each of their answers becomes. Before, every branch raised a bare RuntimeError and
the report read a reason back out of the sentence: "ProofRequestSharingScreen did not appear"
matched no pattern at all, so procivis's "Connection failed" and "No credential interaction
found." were published on 2026-09-24 as plain red cells, and a refused deeplink escaped as a raw
driver exception.

The waits and page objects are replaced by fakes that return each possible answer; the flow code
between them is the real code. The page objects are driven for real against the fake screen in
_procivis_fakes.py. Nothing here touches a device.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from _procivis_fakes import (
    DEAD, PACKAGE, REFUSED, URL, Driver, FakeDriver, Probes, Waits, details_screen, in_front,
    process_screen, wallet_said,
)
from base import deeplink, outcome, progress
from base.android import SystemOverlay
from base.flow_context import FlowContext
from wallets.procivis.flows import _failures as failures
from wallets.procivis.flows import credential_flow as cf, init_flow, verification_flow as vf
from wallets.procivis.pages import credential_accept_process_page as cap
from wallets.procivis.pages import home_page
from wallets.procivis.pages import invitation_error_details_page as details_page
from wallets.procivis.pages import proof_request_accept_process_page as pap
from wallets.procivis.screens import SCREENS

# Taken before any fixture patches them, for the check that they are screens.py's own probes.
_FLOW_FAILURE_PROBE, _FLOW_FAILURE_LABEL = vf._failure_on_screen, failures.failure_label


class _Pin:
    def __init__(self, driver, **kw):
        pass

    def dismiss_biometric_prompt(self):
        pass

    def enter_login_pin(self, pin):
        pass


class _Tap:
    taps = []

    def __init__(self, driver, **kw):
        pass

    def accept(self):
        _Tap.taps.append("accept")

    def share(self):
        _Tap.taps.append("share")


class _Process:
    """The issuance process page: `wait_for_result` returns `result`, or raises `raises`."""
    result, raises = cap.CLEARED, None

    def __init__(self, driver, **kw):
        pass

    def wait_for_result(self):
        if _Process.raises is not None:
            raise _Process.raises
        return _Process.result


class _Share:
    """The share process page: True (success animation seen), False (gone before), or raises."""
    result, raises = True, None

    def __init__(self, driver, **kw):
        pass

    def wait_for_result(self):
        if _Share.raises is not None:
            raise _Share.raises
        return _Share.result


STILL_WORKING = outcome.FlowFailure(outcome.PROCESSING, "[credential_flow] still on its process screen")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    progress.reset()
    _Tap.taps = []
    _Process.result, _Process.raises = cap.CLEARED, None
    _Share.result, _Share.raises = True, None
    _RequestWait.timeouts, _RequestWait.conditions = [], 0
    _RequestWait.timed_out = False
    monkeypatch.setattr(failures, "biometric_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(failures, "pin_screen", SimpleNamespace(on_screen=lambda d, timeout=2: False))
    monkeypatch.setattr(failures, "PinPage", _Pin)
    monkeypatch.setattr(failures, "failure_label", lambda d: "")
    monkeypatch.setattr(failures, "warning_on_screen", lambda d: False)
    monkeypatch.setattr(failures, "read_details", lambda d, **kw: {})
    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: None)
    for m in (cf, vf):
        monkeypatch.setattr(m, "_home_on_screen", lambda d, timeout=2: False)
        monkeypatch.setattr(m, "_failure_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(cf, "CredentialOfferPage", _Tap)
    monkeypatch.setattr(cf, "CredentialAcceptProcessPage", _Process)
    monkeypatch.setattr(cf, "_process_screen_name", lambda d: "InvitationProcessScreen")
    monkeypatch.setattr(vf, "ProofRequestSharingPage", _Tap)
    monkeypatch.setattr(vf, "ProofRequestAcceptProcessPage", _Share)
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield
    progress.reset()


class _RequestWait:
    """Stands in for WebDriverWait in the verification flow. Calls the condition ONCE and returns
    what it returned, or raises TimeoutException for a falsy answer; anything the condition raises
    gets through. Records the timeout asked for, how often the condition was called, and whether
    the wait timed out."""

    timeouts: list = []
    conditions = 0
    timed_out = False

    def __init__(self, driver, timeout):
        self.driver = driver
        _RequestWait.timeouts.append(timeout)

    def until(self, condition):
        _RequestWait.conditions += 1
        answer = condition(self.driver)
        if not answer:
            _RequestWait.timed_out = True
            raise TimeoutException()
        return answer


PAGE_ARGS = {"timeouts": {"credential_offer": 1}}


def _issue(monkeypatch, *waits, process=(False,), offer=(True,), driver=None, pin="123456"):
    """`waits` answers the offer wait, then the dismissal wait. `process` and `offer` answer the
    probes for those two screens, in turn."""
    monkeypatch.setattr(cf, "WebDriverWait", Waits(*waits))
    probes = Probes({cf._process_id: process, cf._offer_id: offer})
    monkeypatch.setattr(cf, "wait_present", probes)
    cf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="credential_issuance", app_package=PACKAGE, pin=pin, **PAGE_ARGS)
    return probes


def _verify(monkeypatch, shown=True, *, process=(True,), invitation=(False,), driver=None,
            pin="123456"):
    """`shown` answers the sharing probe in the proof-request wait, then the re-probes; `process`
    the share process screen; `invitation` the invitation process screen."""
    answers = list(shown) if isinstance(shown, (list, tuple)) else [shown]

    def sharing(d, timeout=2):
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(vf, "sharing_on_screen", sharing)
    monkeypatch.setattr(vf, "WebDriverWait", _RequestWait)
    monkeypatch.setattr(vf, "wait_present", Probes({vf._process_id: process,
                                                     vf._invitation_id: invitation}))
    vf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package=PACKAGE, pin=pin, **PAGE_ARGS)


def _raised(fn, *args, **kwargs):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args, **kwargs)
    return exc.value, progress.snapshot()


def _failure_up(monkeypatch, label="Connection failed", details=None, warning=False):
    for m in (cf, vf):
        monkeypatch.setattr(m, "_failure_on_screen", lambda d, timeout=2: True)
    monkeypatch.setattr(failures, "failure_label", lambda d: label)
    monkeypatch.setattr(failures, "read_details", lambda d, **kw: details or {})
    monkeypatch.setattr(failures, "warning_on_screen", lambda d: warning)


# --- issuance -----------------------------------------------------------------------------------

def test_a_refused_issuance_deeplink_is_unroutable_not_a_raw_driver_exception(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, True, True, driver=Driver(refuse=REFUSED))
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == ""


def test_a_failure_screen_before_any_offer_is_rejected_at_open(monkeypatch):
    """All three issuance reds of 2026-09-24 went this way: straight to the process screen."""
    _failure_up(monkeypatch)
    _Process.result = cap.FAILED
    exc, trail = _raised(_issue, monkeypatch, True, process=(True,))
    assert exc.category == outcome.REJECTED
    assert str(exc).startswith("[rejected] [credential_flow] Credential issuance failed for "
                               "'credential_issuance': \"Connection failed\"")
    assert trail["stage"] == progress.OPEN


@pytest.mark.parametrize("screen", ["InvitationProcessScreen", "CredentialAcceptProcessScreen"])
def test_the_trail_names_the_process_screen_that_actually_matched(monkeypatch, screen):
    """waltid's issuance lands on CredentialAcceptProcessScreen ("Credential offering")."""
    monkeypatch.setattr(cf, "_process_screen_name", lambda d: screen)
    _Process.raises = STILL_WORKING
    _, trail = _raised(_issue, monkeypatch, True, process=(True,))
    assert trail["steps"][-1] == f"open: procivis showed its {screen} before any offer"


def test_a_process_screen_that_never_clears_before_the_offer_is_processing_at_open(monkeypatch):
    _Process.raises = STILL_WORKING
    exc, trail = _raised(_issue, monkeypatch, True, process=(True,))
    assert exc.category == outcome.PROCESSING
    assert trail["stage"] == progress.OPEN


@pytest.mark.parametrize("result, raises, category", [
    (cap.FAILED, None, outcome.REJECTED),
    (None, STILL_WORKING, outcome.PROCESSING),
])
def test_a_failure_after_accept_is_placed_at_the_offer(monkeypatch, result, raises, category):
    _failure_up(monkeypatch)
    _Process.result, _Process.raises = result, raises
    exc, trail = _raised(_issue, monkeypatch, True, True, process=(False, True))
    assert exc.category == category
    assert trail["stage"] == progress.PRESENT
    assert any(s.endswith("tapped Accept") for s in trail["steps"])


def test_an_accept_tap_that_never_lands_does_not_claim_the_offer_was_shown(monkeypatch):
    """`present` is recorded from the tap landing; a tap that fails is no evidence of the offer."""
    class Misses(_Tap):
        def accept(self):
            raise Exception("Element ('xpath', '//*[@resource-id=\"CredentialOfferScreen.accept\"]') "
                            "not clickable after 15s")

    monkeypatch.setattr(cf, "CredentialOfferPage", Misses)
    with pytest.raises(Exception, match="not clickable"):
        _issue(monkeypatch, True, True)
    assert progress.snapshot()["stage"] == progress.DELIVER


def test_an_offer_that_never_closes_after_accept_is_processing_not_rejected(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, True, TimeoutException())
    assert exc.category == outcome.PROCESSING
    assert trail["stage"] == progress.PRESENT


def test_nothing_appearing_while_procivis_shows_its_wallet_screen_is_dismissed(monkeypatch):
    """The offer wait never looks for home, so the shared diagnosis would say "no home screen"
    about a wallet showing it. One look at the end is all it says: not how long it was there."""
    monkeypatch.setattr(cf, "_home_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,))
    assert exc.category == outcome.DISMISSED
    assert "showed its wallet screen, not a credential offer, when checked 1s after the deeplink" \
        in str(exc)
    assert "stayed" not in str(exc) and "no home screen" not in str(exc)
    assert trail["stage"] == progress.OPEN


def test_nothing_appearing_with_something_else_in_front_is_absent(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,),
                         driver=Driver(front="com.android.chrome"))
    assert exc.category == outcome.ABSENT
    assert trail["stage"] == progress.DELIVER


def test_nothing_recognisable_with_procivis_in_front_is_a_timeout_at_open(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,))
    assert exc.category == outcome.PROCESSING
    assert "procivis was in front" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_no_error_is_said_only_after_the_failure_animation_was_looked_for(monkeypatch):
    """The shared diagnosis says "no error"; `_no_offer` makes that true by probing first."""
    asked = []
    monkeypatch.setattr(cf, "_failure_on_screen", lambda d, timeout=2: asked.append(1) or False)
    exc, _ = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,))
    assert "no error" in str(exc)
    assert asked, "said without looking"


@pytest.mark.parametrize("late", ["offer", "process"])
def test_a_screen_that_was_only_late_carries_on_instead_of_failing(monkeypatch, late):
    """A4: the wait ran out a moment before procivis answered."""
    if late == "offer":
        _issue(monkeypatch, TimeoutException(), True, offer=(True,))
    else:
        _issue(monkeypatch, TimeoutException(), True, offer=(False, True), process=(True, False))
    assert _Tap.taps == ["accept"]
    assert progress.snapshot()["stage"] == progress.PRESENT
    assert any(f"showed {'the credential offer' if late == 'offer' else 'its process screen'} late"
               in s for s in progress.snapshot()["steps"])


def test_a_failure_that_lands_as_the_wait_runs_out_is_rejected(monkeypatch):
    _failure_up(monkeypatch)
    exc, trail = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,))
    assert exc.category == outcome.REJECTED
    assert trail["stage"] == progress.OPEN


def test_an_accepted_offer_reaches_present_and_leaves_keep_to_the_count(monkeypatch):
    """procivis's process screen closing is not procivis saying it stored anything."""
    _issue(monkeypatch, True, True, process=(False, True))
    snap = progress.snapshot()
    assert snap["stage"] == progress.PRESENT
    assert snap["steps"][-1].endswith("closed without a failure animation")


def test_a_process_screen_that_cleared_to_home_is_not_tapped_blindly(monkeypatch):
    """A2: after the pre-offer process screen cleared, the offer is looked for, not assumed."""
    monkeypatch.setattr(cf, "_home_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_issue, monkeypatch, True, process=(True, False), offer=(False,))
    assert exc.category == outcome.DISMISSED
    assert "after its process screen closed" in str(exc)
    assert _Tap.taps == []


def test_a_process_screen_that_comes_back_after_clearing_is_processing(monkeypatch):
    exc, _ = _raised(_issue, monkeypatch, True, process=(True,), offer=(False,))
    assert exc.category == outcome.PROCESSING
    assert "showed its process screen again" in str(exc)
    assert _Tap.taps == []


@pytest.mark.parametrize("path, process", [
    ("before the offer", (True,)),
    ("after Accept", (False, True)),
    ("no process screen", (False,)),
])
@pytest.mark.parametrize("overlay, said", [
    (SystemOverlay.APP_CRASH, "crashed"),
    (SystemOverlay.ANR, "stopped responding"),
])
def test_a_crash_dialog_where_a_screen_closed_is_crashed_not_success(
        monkeypatch, path, process, overlay, said):
    """A3: a process screen vanishing under Android's crash dialog used to count as cleared."""
    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: overlay)
    exc, _ = _raised(_issue, monkeypatch, True, True, process=process)
    assert exc.category == outcome.CRASHED
    assert f"procivis {said}" in str(exc)


@pytest.mark.parametrize("dies_in", ["prompts", "offer wait", "dismissal wait", "direct probe",
                                     "after-accept probe", "offer after clearing",
                                     "process page"])
def test_a_dead_session_in_any_issuance_wait_is_ours(monkeypatch, dies_in):
    def dead(d, timeout=2):
        raise DEAD

    kw = {}
    waits = (True, True)
    if dies_in == "prompts":
        monkeypatch.setattr(failures, "biometric_on_screen", dead)
    elif dies_in == "offer wait":
        # `EC.any_of` swallows driver errors, so a dead session ends the offer wait in a timeout;
        # the probe straight after it is what dies, and must not become `absent`.
        waits, kw = (TimeoutException(),), {"offer": (DEAD,)}
    elif dies_in == "dismissal wait":
        waits = (True, DEAD)
    elif dies_in == "direct probe":
        kw = {"process": (DEAD,)}
    elif dies_in == "after-accept probe":
        kw = {"process": (False, DEAD)}
    elif dies_in == "offer after clearing":
        kw = {"process": (True,), "offer": (DEAD,)}
    else:
        _Process.raises = DEAD
        kw = {"process": (True,)}
    exc, _ = _raised(_issue, monkeypatch, *waits, **kw)
    assert exc.category == outcome.LOST_SESSION


@pytest.mark.parametrize("prompt", ["biometric", "pin"])
def test_a_missing_pin_is_our_configuration_not_the_wallet(monkeypatch, prompt):
    if prompt == "biometric":
        monkeypatch.setattr(failures, "biometric_on_screen", lambda d, timeout=2: True)
    else:
        monkeypatch.setattr(failures, "pin_screen", SimpleNamespace(on_screen=lambda d, timeout=2: True))
    exc, _ = _raised(_issue, monkeypatch, True, True, pin="")
    assert exc.category == outcome.SETUP
    assert "pass pin= to credential_flow.run()" in str(exc)


def test_answered_prompts_are_steps_and_the_pin_screen_proves_procivis_came_forward(monkeypatch):
    monkeypatch.setattr(failures, "biometric_on_screen", lambda d, timeout=2: True)
    monkeypatch.setattr(failures, "pin_screen", SimpleNamespace(on_screen=lambda d, timeout=2: True))
    exc, trail = _raised(_issue, monkeypatch, TimeoutException(), offer=(False,),
                         driver=Driver(front="com.android.chrome"))
    # Seen on procivis's own PIN screen, so never "did not come to the foreground" at `deliver`.
    assert trail["stage"] == progress.OPEN
    steps = " | ".join(trail["steps"])
    assert "answered the biometric prompt with USE PIN CODE" in steps
    assert "entered the wallet PIN" in steps


# --- one rejection, one wording, one [no_retry] rule (flows/_failures.py) ------------------------

def _reject(monkeypatch, *, label="Connection failed", details=None, warning=False, stale=False):
    if stale:
        def label_reader(d):
            raise StaleElementReferenceException("stale element reference")
        monkeypatch.setattr(failures, "failure_label", label_reader)
    else:
        monkeypatch.setattr(failures, "failure_label", lambda d: label)
    monkeypatch.setattr(failures, "read_details", lambda d, **kw: details or {})
    monkeypatch.setattr(failures, "warning_on_screen", lambda d: warning)
    ctx = FlowContext(flow="verification_flow", wallet="procivis", what="x")
    with pytest.raises(outcome.FlowFailure) as raised:
        failures.rejected(object(), ctx, head="[verification_flow] head")
    assert raised.value.category == outcome.REJECTED
    return str(raised.value)


AUTHBOUND_CAUSE = ("Error while handling invitation.\nCaused by: Error while fetching offer.\n"
                   "Caused by: HTTP error: error sending request for url "
                   "(https://testbed-issuer.authbound.io/api/v1/openid4vci/credential-offers/4b3b)")
WALTID_CAUSE = ("Error while accepting credential.\nCaused by: Error while requesting credential.\n"
                "Caused by: HTTP status code error: 400, response_body: `{\"exception\":true,"
                "\"id\":\"MissingFieldException\",\"status\":\"Bad Request\",\"code\":\"400\","
                "\"message\":\"Field 'format' is required for type with serial name "
                "'id.walt.oid4vc.requests.CredentialRequest', but it was missing\"}`")
PARADYM_BR_0081 = {"Code": "BR_0081", "Message": "Wallet instance must be active",
                   "Cause": "Error while handling invitation.\nCaused by: Wallet instance required"}


def test_a_transport_failure_keeps_its_reruns(monkeypatch):
    """D2: reqwest's "error sending request" is the request never getting an answer; it passed
    against the same issuer later."""
    text = _reject(monkeypatch, details={"Code": "BR_0347", "Message": "Remote HTTP request failed",
                                         "Cause": AUTHBOUND_CAUSE}, warning=True)
    assert "[no_retry]" not in text
    assert "error sending request" in text


@pytest.mark.parametrize("warning, no_retry", [(True, False), (None, False), (False, True)])
def test_unreadable_details_keep_reruns_unless_the_animation_is_the_hard_error(
        monkeypatch, warning, no_retry):
    """D2: `.animation.warning` (or an animation nobody could read) with no details is unknown."""
    text = _reject(monkeypatch, warning=warning)
    assert text.endswith(" [no_retry]") is no_retry


def test_connection_failed_with_a_readable_deterministic_cause_is_not_retried(monkeypatch):
    """D2: paradym's BR_0081 "Wallet instance must be active" is a verdict, warning or not."""
    text = _reject(monkeypatch, details=PARADYM_BR_0081, warning=True)
    assert text.endswith(" [no_retry]") and text.count("[no_retry]") == 1
    assert "(BR_0081: Wallet instance must be active)" in text


def test_unreadable_details_have_one_wording_in_both_flows(monkeypatch):
    """C5."""
    text = _reject(monkeypatch, label="No credential interaction found.")
    assert text.startswith('[rejected] [verification_flow] head: "No credential interaction found." '
                           "procivis's error details could not be read")


@pytest.mark.parametrize("label", ["Connection failed", "No credential interaction found."])
def test_the_label_is_quoted_first_and_its_own_full_stop_is_not_doubled(monkeypatch, label):
    text = _reject(monkeypatch, label=label)
    assert wallet_said(text) == label
    if label[-1] in ".!?":
        assert f'"{label}".' not in text
    else:
        assert f'"{label}". procivis' in text


def test_a_label_that_goes_stale_is_unquoted_and_still_rejected(monkeypatch):
    """A StaleElementReferenceException is a WebDriverException; it was published as ours."""
    text = _reject(monkeypatch, stale=True)
    assert "label could not be read" in text and '"' not in text


def test_the_label_is_read_before_the_tap_that_leaves_its_screen(monkeypatch):
    calls = []
    monkeypatch.setattr(failures, "failure_label", lambda d: calls.append("label") or "L")
    monkeypatch.setattr(failures, "warning_on_screen", lambda d: calls.append("animation") or False)
    monkeypatch.setattr(failures, "read_details", lambda d, **kw: calls.append("tap ?") or {})
    with pytest.raises(outcome.FlowFailure):
        failures.rejected(object(), FlowContext(flow="f", wallet="procivis", what="x"), head="h")
    assert calls == ["label", "animation", "tap ?"]


# --- verification -------------------------------------------------------------------------------

def test_a_refused_verification_deeplink_is_unroutable(monkeypatch):
    """authbound_verifier's eudi-openid4vp:// link on 2026-09-24, then a raw WebDriverException."""
    exc, trail = _raised(_verify, monkeypatch, driver=Driver(refuse=REFUSED))
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == ""


@pytest.mark.parametrize("label", ["Connection failed", "No credential interaction found."])
def test_procivis_failure_screen_instead_of_the_request_is_rejected_in_its_own_words(
        monkeypatch, label):
    """paradym_verifier and waltid_verifier on 2026-09-24: plain red cells until then."""
    _failure_up(monkeypatch, label=label, details={"Code": "BR_0046", "Message": "Missing"})
    # Both real dumps show the animation *on* InvitationProcessScreen, so the process screen is up
    # too: the failure has to be looked for first, or both would be published as `processing`.
    exc, trail = _raised(_verify, monkeypatch, False, invitation=(True,))
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == label
    assert trail["stage"] == progress.OPEN
    assert str(exc).endswith(" [no_retry]") and "\n" not in str(exc)


def test_details_that_cannot_be_read_never_cost_the_verdict(monkeypatch):
    """The real reader, on a driver that fails every call: label-only, still `rejected`."""
    _failure_up(monkeypatch)
    monkeypatch.setattr(failures, "read_details", cap.read_details)

    class Broken(Driver):
        went_back = 0

        def find_element(self, *a):
            raise DEAD

        def back(self):
            self.went_back += 1

    driver = Broken()
    exc, _ = _raised(_verify, monkeypatch, False, invitation=(True,), driver=driver)
    assert exc.category == outcome.REJECTED
    assert "details could not be read" in str(exc)
    assert driver.went_back == 0, "Back without the details screen open leaves the failure screen"


def test_visible_text_without_a_failure_animation_is_not_a_rejection(monkeypatch):
    """The wallet screen has text too; only the failure animation is procivis's verdict."""
    driver = Driver(front="com.android.chrome", texts=["Connection failed"])
    exc, _ = _raised(_verify, monkeypatch, False, driver=driver)
    assert exc.category == outcome.ABSENT


FRAME_ONLY = ('<hierarchy><android.widget.FrameLayout resource-id="" text="">'
              '<android.widget.LinearLayout '
              'resource-id="ch.procivis.one.wallet.trial:id/action_bar_root" text="">'
              '<android.widget.FrameLayout resource-id="android:id/content" text="" content-desc="" />'
              '</android.widget.LinearLayout></android.widget.FrameLayout></hierarchy>')


@pytest.mark.parametrize("front, category, who", [
    (PACKAGE, outcome.PROCESSING, "procivis exposed"),
    ("com.android.chrome", outcome.ABSENT, "The screen in front exposed"),
])
def test_a_screen_with_nothing_readable_is_said_plainly_and_first(monkeypatch, front, category, who):
    """hovi_verifier and procivis_verifier on 2026-10-09 read as a locator problem. Not called an
    empty tree: a WebView or unnamed images inside the frame would look the same from here."""
    exc, trail = _raised(_verify, monkeypatch, False, driver=Driver(front=front, source=FRAME_ONLY))
    text = str(exc)
    assert exc.category == category
    assert text.startswith(f"[{category}] [verification_flow] {who} nothing readable — no text, "
                           "description or id beyond the activity frame (see the screenshot). ")
    assert text.count("[verification_flow]") == 1, "the shared diagnosis's own tag, not a second"
    assert '"' not in text
    if category == outcome.PROCESSING:
        assert trail["stage"] == progress.OPEN


@pytest.mark.parametrize("source", [
    FRAME_ONLY.replace('resource-id="android:id/content"', 'resource-id="WalletScreen"'),
    FRAME_ONLY.replace('content-desc=""', 'content-desc="Connecting..."'),
    FRAME_ONLY.replace('text="" content-desc', 'text="Connecting..." content-desc'),
    WebDriverException("page source unavailable"),
    "",
])
def test_anything_readable_or_an_unreadable_source_makes_no_empty_claim(monkeypatch, source):
    exc, _ = _raised(_verify, monkeypatch, False, driver=Driver(source=source))
    assert exc.category == outcome.PROCESSING
    assert "nothing readable" not in str(exc)
    assert str(exc).count("[verification_flow]") == 1


def test_visible_text_wins_over_a_frame_only_page_source(monkeypatch):
    """D4: what the text lookup read is evidence; the page source is not consulted over it."""
    driver = Driver(texts=["Connecting..."], source=FRAME_ONLY)
    exc, _ = _raised(_verify, monkeypatch, False, driver=driver)
    assert "nothing readable" not in str(exc)
    assert "Visible text: 'Connecting...'." in str(exc)


@pytest.mark.parametrize("front, category", [
    ("com.android.chrome", outcome.ABSENT),
    (PACKAGE, outcome.PROCESSING),
])
def test_an_unrecognised_screen_says_what_was_on_it_without_putting_words_in_procivis_mouth(
        monkeypatch, front, category):
    driver = Driver(front=front, texts=["Something went wrong", 'He said "hi"', "x" * 200])
    exc, _ = _raised(_verify, monkeypatch, False, driver=driver)
    assert exc.category == category
    assert "Visible text: 'Something went wrong', 'He said 'hi'', …" in str(exc)
    assert str(exc).count("[verification_flow]") == 1
    assert '"' not in str(exc), "the digest would publish it as what the wallet said"


def test_a_stale_element_in_the_text_lookup_is_skipped(monkeypatch):
    driver = Driver(texts=[StaleElementReferenceException("gone"), "Still here"])
    exc, _ = _raised(_verify, monkeypatch, False, driver=driver)
    assert "Visible text: 'Still here'." in str(exc)


def test_the_excerpt_never_follows_an_unroutable_verdict(monkeypatch):
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "not registered")
    exc, _ = _raised(_verify, monkeypatch, False, driver=Driver(texts=["Wallet"]))
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert "Visible text" not in str(exc)


def test_the_process_screen_with_no_failure_animation_is_processing_at_open(monkeypatch):
    exc, trail = _raised(_verify, monkeypatch, False, invitation=(True,))
    assert exc.category == outcome.PROCESSING
    assert "still on its process screen" in str(exc), "seen, not inferred from the foreground"
    assert trail["stage"] == progress.OPEN


def test_a_request_never_shown_while_on_the_wallet_screen_is_dismissed(monkeypatch):
    """C2: one look at the end. True with or without backgrounding before the deeplink."""
    monkeypatch.setattr(vf, "_home_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_verify, monkeypatch, False)
    assert exc.category == outcome.DISMISSED
    assert "showed its wallet screen, not a proof request, when checked 1s after the deeplink" \
        in str(exc)
    assert "stayed" not in str(exc) and "no home screen" not in str(exc)
    assert trail["stage"] == progress.OPEN


@pytest.mark.parametrize("front, category, stage", [
    ("com.android.chrome", outcome.ABSENT, progress.DELIVER),
    (PACKAGE, outcome.PROCESSING, progress.OPEN),
])
def test_a_request_never_shown_and_nothing_recognised_goes_to_the_shared_diagnosis(
        monkeypatch, front, category, stage):
    exc, trail = _raised(_verify, monkeypatch, False, driver=Driver(front=front))
    assert exc.category == category
    assert trail["stage"] == stage


def test_a_request_that_was_only_late_is_shared_as_normal(monkeypatch):
    """A4: the wait ran out a moment before procivis showed the request."""
    _verify(monkeypatch, [False, True])
    assert _Tap.taps == ["share"]
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_failure_animation_ends_the_request_wait_without_waiting_it_out(monkeypatch):
    """waltid_verifier on 2026-10-16: procivis failed within a second of the deeplink and the flow
    noticed 21 s later, because it only waited for the sharing screen."""
    _failure_up(monkeypatch, details={"Code": "BR_0046", "Message": "Missing"})
    exc, trail = _raised(_verify, monkeypatch, False, invitation=(True,))
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Connection failed"
    assert str(exc).endswith(" [no_retry]")
    assert trail["stage"] == progress.OPEN
    assert _RequestWait.timeouts == [1] and _RequestWait.conditions == 1
    assert not _RequestWait.timed_out, "the animation did not end the wait; the timeout did"
    assert _Tap.taps == []


def test_a_failure_flash_before_the_request_still_lets_it_be_shared(monkeypatch):
    """The wait ends on the failure animation; `_no_request` looks for the sharing screen first."""
    monkeypatch.setattr(vf, "_failure_on_screen", lambda d, timeout=2: True)
    _verify(monkeypatch, [False, True])
    assert _Tap.taps == ["share"]
    assert any("showed the proof request late" in s for s in progress.snapshot()["steps"])


def test_the_sharing_screen_first_is_shared_without_any_second_look(monkeypatch):
    _failure_up(monkeypatch)
    _verify(monkeypatch, True)
    assert _Tap.taps == ["share"]
    assert _RequestWait.timeouts == [1] and _RequestWait.conditions == 1
    assert not any("late" in s for s in progress.snapshot()["steps"])


def test_nothing_on_screen_times_out_into_the_existing_diagnosis_naming_the_wait(monkeypatch):
    exc, trail = _raised(_verify, monkeypatch, False, invitation=(True,))
    assert exc.category == outcome.PROCESSING
    assert "after 1s" in str(exc)
    assert _RequestWait.conditions == 1 and _RequestWait.timed_out


# --- after Share ---------------------------------------------------------------------------------

def test_the_success_animation_completes_accept(monkeypatch):
    _verify(monkeypatch, True)
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_silence_on_the_share_process_screen_is_processing_never_rejected(monkeypatch):
    """procivis has no mapped failure surface on its share step."""
    _Share.raises = outcome.FlowFailure(outcome.PROCESSING, "no success animation")
    exc, trail = _raised(_verify, monkeypatch, True)
    assert exc.category == outcome.PROCESSING
    assert trail["stage"] == progress.PRESENT
    assert any(s.endswith("tapped Share") for s in trail["steps"])


@pytest.mark.parametrize("gone", [False, True])
def test_still_on_the_sharing_screen_after_share_is_processing_not_a_pass(monkeypatch, gone):
    """A1: this used to pass. Share disabled or a no-op; no no-match claim without a screen."""
    if gone:
        _Share.result = False
    exc, trail = _raised(_verify, monkeypatch, True, process=(gone,))
    assert exc.category == outcome.PROCESSING
    assert "still on its sharing screen after Share" in str(exc)
    assert outcome.NO_MATCH not in str(exc)
    assert trail["stage"] == progress.PRESENT


def test_a_failure_animation_after_share_is_rejected(monkeypatch):
    _failure_up(monkeypatch, label="Connection failed")
    exc, _ = _raised(_verify, monkeypatch, [True, False], process=(False,))
    assert exc.category == outcome.REJECTED
    assert "after Share" in str(exc)


def test_the_wallet_screen_after_share_with_no_success_animation_is_dismissed(monkeypatch):
    monkeypatch.setattr(vf, "_home_on_screen", lambda d, timeout=2: True)
    exc, _ = _raised(_verify, monkeypatch, [True, False], process=(False,))
    assert exc.category == outcome.DISMISSED
    assert "after Share" in str(exc)


def test_anything_else_after_share_is_processing_with_what_was_visible(monkeypatch):
    exc, _ = _raised(_verify, monkeypatch, [True, False], process=(False,),
                     driver=Driver(texts=["Something else"]))
    assert exc.category == outcome.PROCESSING
    assert "Visible text: 'Something else'." in str(exc)


def test_a_share_process_screen_that_was_only_late_is_waited_on(monkeypatch):
    _verify(monkeypatch, True, process=(False, True))
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_share_process_screen_gone_before_its_result_is_not_waited_for_again(monkeypatch):
    _Share.result = False
    monkeypatch.setattr(vf, "_home_on_screen", lambda d, timeout=2: True)
    exc, _ = _raised(_verify, monkeypatch, [True, False], process=(True,))
    assert exc.category == outcome.DISMISSED
    assert "closed before any result was seen" in str(exc)


@pytest.mark.parametrize("dies_in", ["request wait", "after the wait", "prompts", "after Share",
                                     "share page"])
def test_a_dead_session_in_any_verification_wait_is_ours(monkeypatch, dies_in):
    def dead(d, timeout=2):
        raise DEAD

    if dies_in == "request wait":
        exc, _ = _raised(_verify, monkeypatch, DEAD)
        assert "mid-wait for the proof request" in str(exc)
    elif dies_in == "after the wait":
        exc, _ = _raised(_verify, monkeypatch, False, invitation=(DEAD,))
        assert "after the wait for the proof request" in str(exc)
    elif dies_in == "prompts":
        monkeypatch.setattr(failures, "pin_screen", SimpleNamespace(on_screen=dead))
        exc, _ = _raised(_verify, monkeypatch, True)
    elif dies_in == "after Share":
        exc, _ = _raised(_verify, monkeypatch, True, process=(DEAD,))
    else:
        _Share.raises = DEAD
        exc, _ = _raised(_verify, monkeypatch, True)
    assert exc.category == outcome.LOST_SESSION


@pytest.mark.parametrize("prompt", ["biometric", "pin"])
def test_a_missing_pin_in_verification_is_our_configuration(monkeypatch, prompt):
    if prompt == "biometric":
        monkeypatch.setattr(failures, "biometric_on_screen", lambda d, timeout=2: True)
    else:
        monkeypatch.setattr(failures, "pin_screen", SimpleNamespace(on_screen=lambda d, timeout=2: True))
    exc, _ = _raised(_verify, monkeypatch, True, pin="")
    assert exc.category == outcome.SETUP
    assert "pass pin= to verification_flow.run()" in str(exc)


def test_procivis_flows_and_process_pages_raise_no_bare_runtime_errors():
    root = Path(__file__).parents[2] / "wallets" / "procivis"
    for name in ("flows/credential_flow.py", "flows/verification_flow.py", "flows/_failures.py",
                 "pages/credential_accept_process_page.py",
                 "pages/proof_request_accept_process_page.py"):
        assert "raise RuntimeError(" not in (root / name).read_text(), name


# --- the issuance process page, driven for real --------------------------------------------------

def _process_page(driver, t=2):
    return cap.CredentialAcceptProcessPage(
        driver, timeouts={"credential_offer": t, "default": 1}, debug=False)


class _GoneAfterFirstLook(FakeDriver):
    """Shows the process screen for one look, then procivis has moved on."""

    def find_element(self, by, value):
        found = super().find_element(by, value)
        self.nodes = []
        return found


class _DiesAfterFirstLook(FakeDriver):
    """Finds the process screen once, then every call fails the way a dead session does."""

    def __init__(self, nodes):
        super().__init__(nodes)
        self.looks = 0

    def find_element(self, by, value):
        self.looks += 1
        if self.looks > 1:
            raise DEAD
        return super().find_element(by, value)


class _TimesOut:
    """WebDriverWait whose wait runs out — as it does when a UiAutomator2 blip was swallowed, or
    the screen changed during the last poll's sleep."""

    def __init__(self, driver, timeout):
        pass

    def until(self, condition):
        raise TimeoutException()


def test_the_failure_animation_is_failed_and_nothing_is_tapped_by_the_page():
    driver = FakeDriver(process_screen("warning"))
    assert _process_page(driver).wait_for_result() == cap.FAILED
    assert driver.tapped == []


def test_a_process_screen_that_stays_up_without_a_failure_is_processing():
    driver = FakeDriver([{"resource-id": "InvitationProcessScreen"},
                         {"resource-id": "InvitationProcessScreen.label", "text": "Please wait"}])
    with pytest.raises(outcome.FlowFailure) as raised:
        _process_page(driver, t=0.5).wait_for_result()
    assert raised.value.category == outcome.PROCESSING
    assert driver.tapped == [], "a screen still working is not tapped into"


def test_a_cleared_process_screen_is_cleared_without_a_second_look_for_the_animation(monkeypatch):
    """B3: `_settled` already says which of the two it saw; no extra 1 s probe on success."""
    asked = []
    real = cap.wait_present
    monkeypatch.setattr(cap, "wait_present", lambda d, loc, timeout=2: asked.append(loc) or real(
        d, loc, timeout=timeout))
    driver = _GoneAfterFirstLook([{"resource-id": "InvitationProcessScreen"}])
    assert _process_page(driver, t=1).wait_for_result() == cap.CLEARED
    assert asked == [cap.SCREEN_ID]


def test_a_process_screen_already_gone_is_cleared_without_waiting(monkeypatch):
    """A2: `find` used to wait 15 s here and raise a bare "Element not found"."""
    class NoWait:
        def __init__(self, *a):
            raise AssertionError("waited on a screen that was already gone")

    monkeypatch.setattr(cap, "WebDriverWait", NoWait)
    assert _process_page(FakeDriver([])).wait_for_result() == cap.CLEARED
    assert "already closed" in progress.snapshot()["steps"][-1]


def test_a_dead_session_on_the_process_screen_propagates_for_the_flow_to_name():
    """`_settled` swallows the dead session's errors, so the wait ends in a timeout; the re-probe
    then raises, and the flow makes it `lost_session` (see the issuance tests above)."""
    driver = _DiesAfterFirstLook([{"resource-id": "InvitationProcessScreen"}])
    with pytest.raises(WebDriverException):
        _process_page(driver, t=0.5).wait_for_result()


def test_a_process_screen_gone_by_the_re_probe_is_cleared(monkeypatch):
    """Session live, screen gone: most likely it cleared in the last sleep. Not `processing`."""
    monkeypatch.setattr(cap, "WebDriverWait", _TimesOut)
    driver = _GoneAfterFirstLook([{"resource-id": "InvitationProcessScreen"}])
    assert _process_page(driver, t=0.5).wait_for_result() == cap.CLEARED
    assert "cleared only after the wait ran out" in progress.snapshot()["steps"][-1]


def test_a_failure_animation_found_by_the_re_probe_is_still_failed(monkeypatch):
    """The animation sits on the process screen, so a re-probe of the screen alone would call
    this `processing` and lose the label, the Code/Message/Cause and [no_retry]."""
    monkeypatch.setattr(cap, "WebDriverWait", _TimesOut)
    assert _process_page(FakeDriver(process_screen("warning"))).wait_for_result() == cap.FAILED


# --- the share process page, driven for real -----------------------------------------------------

def _share_page(driver, t=0.5):
    return pap.ProofRequestAcceptProcessPage(
        driver, timeouts={"credential_offer": t, "default": 1}, debug=False)


def test_no_success_animation_after_share_is_processing_and_says_it_is_no_verdict():
    driver = FakeDriver([{"resource-id": "ProofRequestAcceptProcessScreen"}])
    with pytest.raises(outcome.FlowFailure) as raised:
        _share_page(driver).wait_for_result()
    assert raised.value.category == outcome.PROCESSING
    assert "not a verdict" in str(raised.value)
    assert str(raised.value).startswith("[processing] [verification_flow] ")


def test_the_success_animation_returns_true():
    driver = FakeDriver([{"resource-id": "ProofRequestAcceptProcessScreen"},
                         {"resource-id": "ProofRequestAcceptProcessScreen.animation.success"}])
    assert _share_page(driver).wait_for_result() is True


def test_a_share_screen_already_gone_is_false_without_waiting(monkeypatch):
    class NoWait:
        def __init__(self, *a):
            raise AssertionError("waited on a screen that was already gone")

    monkeypatch.setattr(pap, "WebDriverWait", NoWait)
    assert _share_page(FakeDriver([])).wait_for_result() is False


def test_a_dead_session_before_the_success_animation_propagates():
    driver = _DiesAfterFirstLook([{"resource-id": "ProofRequestAcceptProcessScreen"}])
    with pytest.raises(WebDriverException):
        _share_page(driver).wait_for_result()


def test_a_dead_session_after_the_success_animation_is_ignored():
    """A5: procivis already said the share succeeded."""
    class DiesAfterSuccess(FakeDriver):
        seen = False

        def find_element(self, by, value):
            if self.seen:
                raise DEAD
            found = super().find_element(by, value)
            if "animation.success" in value:
                self.seen = True
            return found

    driver = DiesAfterSuccess([{"resource-id": "ProofRequestAcceptProcessScreen"},
                               {"resource-id": "ProofRequestAcceptProcessScreen.animation.success"}])
    assert _share_page(driver).wait_for_result() is True


# --- what screens.py hands the shared code -------------------------------------------------------

def test_the_flows_read_failure_with_the_same_probes_the_digest_uses():
    """One definition of procivis's failure screen and its words, not a second copy."""
    assert _FLOW_FAILURE_PROBE is SCREENS.error is cap.present
    assert _FLOW_FAILURE_LABEL is SCREENS.error_text is cap.message


def test_the_quote_helper_never_quotes_a_placeholder():
    assert cap.quoted("Connection failed") == ': "Connection failed"'
    assert '"' not in cap.quoted("")


@pytest.mark.parametrize("animation, warning", [("warning", True), ("error", False)])
def test_which_failure_animation_is_up_is_read(animation, warning):
    assert cap.warning_on_screen(FakeDriver(process_screen(animation))) is warning


def test_an_unreadable_animation_is_none_not_a_claim():
    class Dead(FakeDriver):
        def find_elements(self, by, value):
            raise DEAD

    assert cap.warning_on_screen(Dead([])) is None


# --- an empty wallet, in procivis's own words -----------------------------------------------------
#
# Nodes copied from reports/2026-09-18_15-37-26/procivis/xml_dumps/
# test_credential_verification_authbound_verifier_pension_verification-procivis.xml (reports/ is
# not tracked, so the dump cannot be read from here).

_HEADER = [{"resource-id": "WalletScreen"}, {"resource-id": "WalletScreen.header"},
           {"resource-id": "WalletScreen.header.title", "text": "Wallet"}]
_EMPTY = _HEADER + [
    {"resource-id": "WalletScreen.empty.title", "text": "No credentials yet"},
    {"resource-id": "WalletScreen.empty.subtitle",
     "text": "Connect to an issuer to add your first credential."},
]
_ONE_CARD = _HEADER + [{"resource-id": "WalletScreen.credential.7e07.card"}]


def test_procivis_empty_state_is_read_from_its_own_copy():
    assert home_page.empty_on_screen(FakeDriver(_EMPTY), timeout=0.1)
    assert not home_page.empty_on_screen(FakeDriver(_HEADER), timeout=0.1)


@pytest.mark.parametrize("nodes, count, empty", [
    (_EMPTY, 0, True),        # procivis says so: nothing_to_present
    (_HEADER, 0, False),      # header up, no cards yet — a list still rendering reads 0 too
    (_EMPTY, None, False),    # unreadable count: the flow decides
    (_HEADER, 2, False),
])
def test_only_a_zero_that_procivis_confirms_is_an_empty_wallet(nodes, count, empty):
    assert home_page.holds_nothing(FakeDriver(nodes), count) is empty


def _precondition(nodes):
    failures.require_something_to_present(
        FakeDriver(nodes), {"timeouts": {"default": 0.1}}, issuer="paradym_verifier",
        case="pension_verification")


def test_an_empty_wallet_in_procivis_own_words_is_nothing_to_present(caplog):
    """The verification test's empty-wallet branch, behaviourally: the category is asserted."""
    with pytest.raises(outcome.FlowFailure) as raised:
        _precondition(_EMPTY)
    assert raised.value.category == outcome.NOTHING_TO_PRESENT
    assert '"' not in str(raised.value), "our words, not procivis's: never in double quotes"
    assert "procivis holds 0 credentials before verification" in caplog.text


@pytest.mark.parametrize("nodes, logged", [
    (_HEADER, "Count read 0 but procivis showed no empty state"),
    (_ONE_CARD, "procivis holds 1 credentials before verification"),
    ([], "count before verification could not be read"),
])
def test_anything_else_goes_on_to_the_verifier_and_leaves_the_count_in_test_log(
        caplog, nodes, logged):
    _precondition(nodes)
    assert logged in caplog.text


# --- procivis's Code/Message/Cause, shared by both flows ----------------------------------------

def test_the_cause_chain_is_one_line_with_the_deepest_cause_first():
    line = cap.details_line({"Code": "BR_0347", "Message": "Remote HTTP request failed",
                             "Cause": AUTHBOUND_CAUSE})
    assert "\n" not in line
    assert line.startswith("HTTP error: error sending request for url (https://testbed-issuer.authbound.io")
    assert line.endswith("← Error while fetching offer. ← Error while handling invitation. "
                         "(BR_0347: Remote HTTP request failed)")


def test_a_json_response_body_is_reduced_to_its_message_and_loses_its_double_quotes():
    line = cap.details_line({"Code": "BR_0395", "Message": "Remote HTTP request status failure (4xx)",
                             "Cause": WALTID_CAUSE})
    assert line.startswith("HTTP status code error: 400 — Field 'format' is required for type")
    assert '"' not in line
    assert "MissingFieldException" not in line


@pytest.mark.parametrize("details, line", [
    ({}, ""),
    ({"Code": "BR_0395", "Message": "Remote HTTP request status failure (4xx)"},
     "Remote HTTP request status failure (4xx) (BR_0395)"),
    ({"Code": "BR_1"}, "BR_1"),
    ({"Message": 'Say "no"', "Extra": "a\nb"}, "Say 'no'; Extra: a b"),
])
def test_details_line_shapes(details, line):
    assert cap.details_line(details) == line


def _body(obj, indent=None):
    return "HTTP status code error: 400, response_body: `" + json.dumps(obj, indent=indent) + "`"


def test_a_multi_line_json_message_cannot_push_code_and_no_retry_off_the_published_line():
    line = cap.details_line({"Code": "BR_0395", "Message": "Remote HTTP request status failure (4xx)",
                             "Cause": "Error while accepting credential.\nCaused by: "
                                      + _body({"message": "line1\nline2"})})
    assert "\n" not in line
    assert line == ("HTTP status code error: 400 — line1 line2 ← Error while accepting credential. "
                    "(BR_0395: Remote HTTP request status failure (4xx))")


def test_a_pretty_printed_body_stays_one_link_and_nothing_is_dropped():
    cause = "Error while accepting credential.\nCaused by: " + _body(
        {"exception": True, "message": "Field 'format' is required"}, indent=2)
    line = cap.details_line({"Code": "BR_0395", "Cause": cause})
    assert line == ("HTTP status code error: 400 — Field 'format' is required ← "
                    "Error while accepting credential. (BR_0395)")


@pytest.mark.parametrize("body, said", [
    ({"message": "", "error_description": "bad proof"}, "bad proof"),
    ({"message": "  ", "error": "invalid_request"}, "invalid_request"),
    ({"detail": "nope"}, "nope"),
])
def test_an_empty_message_key_falls_back_to_the_next(body, said):
    assert cap.details_line({"Cause": _body(body)}) == f"HTTP status code error: 400 — {said}"


def test_a_body_that_starts_the_line_has_no_leading_dash():
    assert cap._compact("response_body: `" + json.dumps({"message": "boom"}) + "`") == "boom"


def test_a_missing_attribute_renders_as_nothing_not_none():
    assert cap.details_line({"Code": "BR_1", "Message": None, "Cause": None, "Extra": None}) == "BR_1"


class _Recording(FakeDriver):
    def __init__(self, nodes, on_tap=None, collect_fails=False):
        super().__init__(nodes, on_tap)
        self.collect_fails = collect_fails

    def find_elements(self, by, value):
        if self.collect_fails and "attributeLabel" in value:
            raise DEAD
        return super().find_elements(by, value)


def test_read_details_taps_the_question_mark_then_the_expander_and_leaves_the_details_up():
    """Both flows use this. The details screen, cause expanded, is the failure evidence."""
    on_tap = {"InvitationProcessScreen.header.info": details_screen()}
    driver = _Recording(process_screen("error", "No credential interaction found."), on_tap)
    details = cap.read_details(driver, timeouts={"default": 0.5})
    assert driver.tapped == ["InvitationProcessScreen.header.info",
                             "InvitationErrorDetailsScreen.cause.expandValueButton"]
    assert driver.went_back == 0
    assert in_front(driver, "InvitationErrorDetailsScreen")
    assert details["Code"] == "BR_0395"


class _ClickSpy:
    """Records BasePage.click's locators and timeouts instead of measuring wall-clock time."""

    def __init__(self, monkeypatch):
        self.calls = []
        real = cap.BasePage.click

        def click(page, locator, timeout=None, attempts=3):
            self.calls.append((locator, timeout))
            return real(page, locator, timeout=timeout, attempts=attempts)

        monkeypatch.setattr(cap.BasePage, "click", click)


def test_a_missing_expander_costs_one_short_look_not_the_click_timeout(monkeypatch):
    """B1: the configured default here is 10 s; the expander is looked for for 1 s, not clicked."""
    spy = _ClickSpy(monkeypatch)
    probes = []
    real = cap.wait_present
    monkeypatch.setattr(cap, "wait_present", lambda d, loc, timeout=2: probes.append(
        (loc, timeout)) or real(d, loc, timeout=min(timeout, 0.1)))
    on_tap = {"InvitationProcessScreen.header.info": details_screen(expandable=False)}
    driver = FakeDriver(process_screen("warning"), on_tap)
    assert cap.read_details(driver, timeouts={"default": 10})["Code"] == "BR_0395"
    assert (cap._show_more, 1) in probes
    assert all(locator != cap._show_more for locator, _ in spy.calls)


def test_a_present_expander_is_tapped_with_a_short_timeout(monkeypatch):
    spy = _ClickSpy(monkeypatch)
    on_tap = {"InvitationProcessScreen.header.info": details_screen()}
    cap.read_details(FakeDriver(process_screen("warning"), on_tap), timeouts={"default": 10})
    assert (cap._show_more, 2) in spy.calls


def test_read_details_without_a_question_mark_taps_nothing_and_stays_put(monkeypatch):
    spy = _ClickSpy(monkeypatch)
    nodes = [n for n in process_screen("warning") if not n["resource-id"].endswith(".header.info")]
    driver = FakeDriver(nodes)
    assert cap.read_details(driver, timeouts={"default": 5}) == {}
    assert spy.calls == [] and driver.went_back == 0


def test_a_tap_procivis_ignored_leaves_the_failure_screen_alone(monkeypatch):
    """UiAutomator2 dispatched the tap, the details screen never came up: nothing else is touched,
    so teardown records the failure screen."""
    spy = _ClickSpy(monkeypatch)
    driver = FakeDriver(process_screen("warning"))          # tapping "?" changes nothing
    assert cap.read_details(driver, timeouts={"default": 5}) == {}
    assert [locator for locator, _ in spy.calls] == [cap._info]
    assert driver.went_back == 0
    assert in_front(driver, "InvitationProcessScreen")


def test_read_details_that_fail_after_the_tap_never_raise_and_leave_the_details_up():
    on_tap = {"InvitationProcessScreen.header.info": details_screen()}
    driver = _Recording(process_screen("warning"), on_tap, collect_fails=True)
    assert cap.read_details(driver, timeouts={"default": 0.5}) == {}
    assert driver.went_back == 0
    assert in_front(driver, "InvitationErrorDetailsScreen")


@pytest.mark.parametrize("nodes, name", [
    (process_screen("warning"), "InvitationProcessScreen"),
    ([{"resource-id": "CredentialAcceptProcessScreen"},
      {"resource-id": "CredentialAcceptProcessScreen.header.close", "text": "Credential offering"}],
     "CredentialAcceptProcessScreen"),
])
def test_screen_name_reads_which_process_screen_is_up(nodes, name):
    assert cap.screen_name(FakeDriver(nodes)) == name


def test_screen_name_names_no_screen_it_could_not_read():
    class Dead(FakeDriver):
        def find_elements(self, by, value):
            raise DEAD

    assert cap.screen_name(Dead([])) == "process screen"


# --- teardown gets procivis home from the screens the flows leave up ------------------------------

class _Screens:
    """Screens as sets of locators, and where tapping one of them, or Back, leads."""

    def __init__(self, screens, start, taps, backs):
        self.screens, self.screen, self.taps, self.backs = screens, start, taps, backs
        self.tapped, self.went_back = [], 0
        self.current_package = PACKAGE

    def find_element(self, by, value):
        if (by, value) not in self.screens[self.screen]:
            raise NoSuchElementException(value)
        driver = self

        class _El:
            def click(self):
                driver.tapped.append(value)
                driver.screen = driver.taps.get((driver.screen, value), driver.screen)

        return _El()

    def back(self):
        self.went_back += 1
        self.screen = self.backs.get(self.screen, self.screen)


def _teardown_screens(failure_screen="invitation"):
    """InvitationProcessScreen has a bottom Close; CredentialAcceptProcessScreen only the header
    close (content-desc "close", lowercase), as in the 2026-10-09 dumps."""
    if failure_screen == "invitation":
        failure = {init_flow._invitation_close, init_flow._header_close, cap.SCREEN_ID}
        close = init_flow._invitation_close
    else:
        failure = {init_flow._header_close, cap.SCREEN_ID}
        close = init_flow._header_close
    return _Screens(
        screens={
            "details": {details_page.CLOSE_ID, details_page.SCREEN_ID},
            "failure": failure,
            "home": {init_flow._home_heading},
        },
        start="details",
        taps={("details", details_page.CLOSE_ID[1]): "failure", ("failure", close[1]): "home"},
        backs={"details": "failure", "failure": "home"},
    )


def _instant_probes(monkeypatch):
    monkeypatch.setattr(init_flow, "biometric_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(init_flow, "wait_present",
                        lambda d, loc, timeout=2: loc in d.screens[d.screen])


@pytest.mark.parametrize("failure_screen, close", [
    ("invitation", init_flow._invitation_close),
    ("credential_accept", init_flow._header_close),
])
def test_teardown_closes_the_details_screen_then_the_failure_screen_and_reaches_home(
        monkeypatch, failure_screen, close):
    """B2: waltid's CredentialAcceptProcessScreen used to fall through to Back presses."""
    driver = _teardown_screens(failure_screen)
    _instant_probes(monkeypatch)
    assert init_flow._back_to_known_state(driver, PACKAGE) == "home"
    assert driver.tapped == [details_page.CLOSE_ID[1], close[1]]
    assert driver.went_back == 0, "closed by its own controls, not by blind Back presses"


def test_closing_the_details_screen_is_a_no_op_anywhere_else():
    driver = _teardown_screens()
    driver.screen = "failure"
    assert init_flow._close_error_details(driver) is False
    assert driver.tapped == []


def test_the_teardown_locators_are_procivis_own_ids():
    """Pinned by value: the teardown tests above model the screens with these same locators, so
    they cannot notice one changing. Checked against the 2026-10-09 and 2026-10-14 dumps."""
    assert details_page.CLOSE_ID == (
        AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen.closeIcon"]')
    assert init_flow._header_close == (
        AppiumBy.XPATH, '//*[contains(@resource-id, "ProcessScreen.header.close")]')


def test_if_the_close_icon_is_not_found_back_presses_still_reach_home(monkeypatch):
    driver = _teardown_screens()
    driver.screens["details"] = {details_page.SCREEN_ID}       # icon not where it was expected
    driver.screens["failure"] = {cap.SCREEN_ID}                 # nor any close
    _instant_probes(monkeypatch)
    assert init_flow._back_to_known_state(driver, PACKAGE) == "home"
    assert driver.went_back == 2, "details -> failure screen -> home"
