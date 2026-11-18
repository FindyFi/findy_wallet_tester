"""Every way authbound's flows can end, mapped to the outcome it raises and how far it got.

authbound kept its own polling loops — `_wait_for_result` / `_wait_for_request` tell error, offer,
home and timeout apart, and `wait_for_outcome` carries the per-document authentication budget —
and those loops are unchanged. What changed is what each of their answers becomes. Every branch
used to raise a bare RuntimeError, so the report read a reason back out of the sentence, and one
of those readings was wrong: "neither the success nor the error screen appeared" was published as
`rejected`, because it contains the words "error screen".

The loops and page objects are replaced here by fakes that return each possible answer; the flow
code between them is the real code. Nothing here touches a device.
"""
import time
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from wallets.authbound.flows import credential_flow as cf, verification_flow as vf

URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example"


class Driver:
    """`front` is what `current_package` reports; `refuse` is what firing the deeplink raises."""
    capabilities: dict = {}

    def __init__(self, refuse=None, front="io.authbound.wallet"):
        self.refuse = refuse
        self.current_package = front

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException(self.refuse)


# The real shape, from reports/2026-09-17_15-13-01/unime/test.log: two lines, the refusal second.
REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=openid-credential-offer:///... "
           "pkg=io.authbound.wallet }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=openid-credential-offer:///... flg=0x10000000 "
           "pkg=io.authbound.wallet }")


class _Error:
    def __init__(self, driver, **kw):
        pass

    def get_error_text(self):
        return "An unexpected error has occurred. Please try again."


class _Offer:
    def __init__(self, driver, **kw):
        pass

    def accept(self):
        pass


class _Success:
    def __init__(self, driver, **kw):
        pass

    def added_document_names(self):
        return ["Eläkeläistodiste"]

    def close(self):
        pass


class _Request:
    def __init__(self, driver, **kw):
        pass

    def requested_document_count(self):
        return 2

    def share(self):
        pass


class _Home:
    def __init__(self, driver, **kw):
        pass

    def wait_until_loaded(self):
        pass


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    progress.reset()
    # No real sleeping, no scheme dump through adb: `unroutable_reason` is set per test.
    fake_time = SimpleNamespace(sleep=lambda s: None, time=time.time)
    for m in (cf, vf):
        monkeypatch.setattr(m, "_time", fake_time)
        monkeypatch.setattr(m, "ErrorPage", _Error)
        monkeypatch.setattr(m, "DocumentSuccessPage", _Success)
        monkeypatch.setattr(m, "authenticate_with_pin", lambda *a, **k: True)
        monkeypatch.setattr(m, "handle_biometric_if_present", lambda *a, **k: False)
        monkeypatch.setattr(m, "in_biometric_enrollment", lambda *a, **k: False)
    monkeypatch.setattr(cf, "CredentialOfferPage", _Offer)
    monkeypatch.setattr(cf, "dismiss_biometric_enrollment", lambda *a, **k: None)
    monkeypatch.setattr(vf, "VerificationRequestPage", _Request)
    monkeypatch.setattr(vf, "HomePage", _Home)
    monkeypatch.setattr(vf, "_no_match_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield
    progress.reset()


PAGE_ARGS = {"timeouts": {"credential_offer": 1}, "device_pin": "0000"}


def _issue(monkeypatch, first, after=None, driver=None):
    monkeypatch.setattr(cf, "_wait_for_result", lambda *a, **k: first)
    monkeypatch.setattr(cf, "wait_for_outcome", lambda *a, **k: after)
    cf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="credential_issuance", app_package="io.authbound.wallet", **PAGE_ARGS)


def _verify(monkeypatch, first, after=None, driver=None):
    monkeypatch.setattr(vf, "_wait_for_request", lambda *a, **k: first)
    monkeypatch.setattr(vf, "wait_for_outcome", lambda *a, **k: after)
    vf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package="io.authbound.wallet", **PAGE_ARGS)


def _raised(fn, *args, **kwargs):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args, **kwargs)
    return exc.value, progress.snapshot()


# --- issuance -----------------------------------------------------------------------------------

@pytest.mark.parametrize("first, after, category, stage", [
    ("error", None, outcome.REJECTED, progress.OPEN),
    ("home", None, outcome.DISMISSED, progress.OPEN),
    ("timeout", None, outcome.PROCESSING, progress.OPEN),   # authbound is in front: see below
    ("offer", "error", outcome.REJECTED, progress.PRESENT),
    ("offer", "prompt_loop", outcome.PROMPT_LOOP, progress.PRESENT),
    ("offer", "timeout", outcome.PROCESSING, progress.PRESENT),
])
def test_every_issuance_ending_is_a_named_outcome_at_the_stage_it_reached(
        monkeypatch, first, after, category, stage):
    exc, trail = _raised(_issue, monkeypatch, first, after)
    assert exc.category == category
    assert trail["stage"] == stage


def test_the_post_authentication_timeout_names_itself(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, "offer", "timeout")
    assert exc.category == outcome.PROCESSING
    assert "neither the success nor the error screen appeared" in str(exc)
    assert "tapped Add" in " ".join(trail["steps"])


def test_a_prompt_loop_keeps_its_no_retry_tag(monkeypatch):
    """Deterministic on this device: the suite's reruns must still skip it."""
    exc, _ = _raised(_issue, monkeypatch, "offer", "prompt_loop")
    assert str(exc).endswith("[no_retry]")


def test_an_enrollment_wizard_is_a_precondition_not_the_wallets_verdict(monkeypatch):
    monkeypatch.setattr(cf, "in_biometric_enrollment", lambda *a, **k: True)
    exc, _ = _raised(_issue, monkeypatch, "offer", "timeout")
    assert exc.category == outcome.SETUP
    assert str(exc).endswith("[no_retry]")


@pytest.mark.parametrize("run, first, after", [
    ("issue", "error", None), ("issue", "offer", "error"),
    ("verify", "error", None), ("verify", "request", "error"),
])
def test_the_wallets_error_copy_is_quoted_for_the_digest(monkeypatch, run, first, after):
    """The app.log digest reads the first double-quoted span as what the wallet said."""
    exc, _ = _raised(_issue if run == "issue" else _verify, monkeypatch, first, after)
    assert '"An unexpected error has occurred. Please try again."' in str(exc)


def test_an_unreadable_error_screen_is_never_quoted_as_the_wallets_words(monkeypatch):
    """`get_error_text` returns "(no error text)" when it reads nothing; quoted, the digest would
    publish `wallet said: "(no error text)"`."""
    class Silent(_Error):
        def get_error_text(self):
            return "(no error text)"

    monkeypatch.setattr(cf, "ErrorPage", Silent)
    exc, _ = _raised(_issue, monkeypatch, "error")
    assert '"(no error text)"' not in str(exc)
    assert "(no error text)" in str(exc)


def test_error_page_message_does_not_pass_placeholders_on(monkeypatch):
    from wallets.authbound.pages import error_page
    monkeypatch.setattr(error_page, "on_screen", lambda d, timeout=2: True)
    monkeypatch.setattr(error_page.ErrorPage, "get_error_text", lambda self: "(no error text)")
    assert error_page.message(object()) == ""
    monkeypatch.setattr(error_page.ErrorPage, "get_error_text", lambda self: "Something broke")
    assert error_page.message(object()) == "Something broke"


def test_a_success_completes_accept_and_raises_nothing(monkeypatch):
    _issue(monkeypatch, "offer", "success")
    assert progress.snapshot()["stage"] == progress.ACCEPT


@pytest.mark.parametrize("run", ["issue", "verify"])
def test_android_refusing_the_link_is_unroutable_on_its_own_word(monkeypatch, run):
    """With the package named, "unable to resolve Intent" is the device saying this URL cannot
    reach authbound. It used to be described as a 30-second wait that never happened."""
    go = _issue if run == "issue" else _verify
    exc, trail = _raised(go, monkeypatch, "offer", "success", driver=Driver(refuse=REFUSED))
    assert exc.category == outcome.UNROUTABLE
    assert "unable to resolve Intent" in str(exc), "Android's own words"
    assert "after 30s" not in str(exc) and "Nothing appeared" not in str(exc), "no wait happened"
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == "", "nothing was delivered"


@pytest.mark.parametrize("run", ["issue", "verify"])
def test_any_other_error_firing_the_link_is_the_session_not_a_verdict(monkeypatch, run):
    go = _issue if run == "issue" else _verify
    exc, _ = _raised(go, monkeypatch, "offer", "success",
                     driver=Driver(refuse="instrumentation process is not running"))
    assert exc.category == outcome.LOST_SESSION


def test_nothing_recognisable_is_checked_for_unroutable_before_blaming_the_wallet(monkeypatch):
    monkeypatch.setattr(deeplink, "unroutable_reason",
                        lambda *a, **k: "authbound registers only openid4vp")
    exc, _ = _raised(_issue, monkeypatch, "timeout")
    assert exc.category == outcome.UNROUTABLE


def test_a_timeout_with_authbound_in_front_is_not_published_as_never_coming_forward(monkeypatch):
    """unime printed "did not come to the foreground (com.impierce.identity_wallet was)" on
    2026-09-24 — its own package. authbound is fired while already in front, so it would too."""
    exc, trail = _raised(_issue, monkeypatch, "timeout")
    assert exc.category == outcome.PROCESSING
    assert "did not come to the foreground" not in str(exc)
    assert "authbound was in front" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_a_timeout_with_something_else_in_front_is_absent(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, "timeout", driver=Driver(front="com.motorola.launcher3"))
    assert exc.category == outcome.ABSENT
    assert "com.motorola.launcher3 was" in str(exc)
    assert trail["stage"] == progress.DELIVER


def test_a_dead_session_in_the_wait_is_ours_not_the_wallets(monkeypatch):
    """The real `_wait_for_result`, with its first probe finding the session gone."""
    def dead(*a, **k):
        raise WebDriverException("instrumentation process is not running")

    monkeypatch.setattr(cf, "handle_biometric_if_present", dead)
    exc, _ = _raised(cf.run, Driver(), provider=SimpleNamespace(get=lambda n: URL),
                     credential_name="c", app_package="io.authbound.wallet", **PAGE_ARGS)
    assert exc.category == outcome.LOST_SESSION


# --- verification -------------------------------------------------------------------------------

@pytest.mark.parametrize("first, after, category, stage", [
    ("error", None, outcome.REJECTED, progress.OPEN),
    ("home", None, outcome.DISMISSED, progress.OPEN),
    ("timeout", None, outcome.PROCESSING, progress.OPEN),
    ("request", "error", outcome.REJECTED, progress.MATCH),
    ("request", "prompt_loop", outcome.PROMPT_LOOP, progress.MATCH),
    ("request", "timeout", outcome.PROCESSING, progress.MATCH),
])
def test_every_verification_ending_is_a_named_outcome_at_the_stage_it_reached(
        monkeypatch, first, after, category, stage):
    exc, trail = _raised(_verify, monkeypatch, first, after)
    assert exc.category == category
    assert trail["stage"] == stage


def test_an_empty_request_is_no_match_and_stays_unretried(monkeypatch):
    """authbound issues a batch; a spent credential does not refill on a retry."""
    monkeypatch.setattr(vf, "_no_match_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_verify, monkeypatch, "request", "success")
    assert exc.category == outcome.NO_MATCH
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == progress.PRESENT, "the request was shown; nothing matched it"


def test_a_shared_presentation_completes_accept(monkeypatch):
    _verify(monkeypatch, "request", "success")
    snap = progress.snapshot()
    assert snap["stage"] == progress.ACCEPT
    assert any("2 document(s)" in s for s in snap["steps"])


def test_an_enrollment_wizard_after_sharing_is_a_precondition(monkeypatch):
    monkeypatch.setattr(vf, "in_biometric_enrollment", lambda *a, **k: True)
    exc, _ = _raised(_verify, monkeypatch, "request", "success")
    assert exc.category == outcome.SETUP
    assert str(exc).endswith("[no_retry]")


def test_an_enrollment_wizard_that_breaks_authentication_is_a_precondition(monkeypatch):
    """The PIN is often accepted and Android *then* opens the wizard, failing the auth call."""
    def fails(*a, **k):
        raise RuntimeError("auth UI never closed")

    monkeypatch.setattr(vf, "authenticate_with_pin", fails)
    monkeypatch.setattr(vf, "in_biometric_enrollment", lambda *a, **k: True)
    exc, _ = _raised(_verify, monkeypatch, "request", "success")
    assert exc.category == outcome.SETUP
    assert str(exc).endswith("[no_retry]")


def test_a_verification_prompt_loop_keeps_its_no_retry_tag(monkeypatch):
    exc, _ = _raised(_verify, monkeypatch, "request", "prompt_loop")
    assert exc.category == outcome.PROMPT_LOOP
    assert str(exc).endswith("[no_retry]")


def test_a_dead_session_in_the_request_wait_is_ours_not_the_wallets(monkeypatch):
    """The real `_wait_for_request`, with its first probe finding the session gone."""
    def dead(*a, **k):
        raise WebDriverException("instrumentation process is not running")

    monkeypatch.setattr(vf, "wait_present", dead)
    exc, _ = _raised(vf.run, Driver(), provider=SimpleNamespace(get=lambda n: URL),
                     credential_name="v", app_package="io.authbound.wallet", **PAGE_ARGS)
    assert exc.category == outcome.LOST_SESSION


def test_an_uncountable_document_list_is_not_claimed_as_a_match(monkeypatch):
    class Uncounted(_Request):
        def requested_document_count(self):
            return 0

    monkeypatch.setattr(vf, "VerificationRequestPage", Uncounted)
    exc, trail = _raised(_verify, monkeypatch, "request", "timeout")
    assert trail["stage"] == progress.PRESENT
    assert "could not be counted" in " ".join(trail["steps"])


def test_authbounds_flows_raise_no_bare_runtime_errors():
    """Every ending is named. A bare RuntimeError would send the cell back to the prose reading."""
    from pathlib import Path
    root = Path(__file__).parents[2] / "wallets" / "authbound" / "flows"
    for name in ("credential_flow.py", "verification_flow.py"):
        assert "raise RuntimeError(" not in (root / name).read_text(), name


# --- what the message claims about authentication ---------------------------------------------
#
# On 2026-09-25 authbound rejected every offer ~16 s after Add with no authentication prompt at
# all, and the message said "after authentication". It now says what the trail recorded.

def _no_prompt(monkeypatch, module):
    monkeypatch.setattr(module, "authenticate_with_pin", lambda *a, **k: False)
    monkeypatch.setattr(module, "handle_biometric_if_present", lambda *a, **k: False)


def test_a_rejection_with_no_prompt_does_not_claim_authentication(monkeypatch):
    _no_prompt(monkeypatch, cf)
    exc, _ = _raised(_issue, monkeypatch, "offer", "error")
    assert "after Add, with no authentication recorded after it" in str(exc)
    assert "after Add and device authentication" not in str(exc)


def test_a_rejection_after_an_answered_prompt_says_so(monkeypatch):
    exc, _ = _raised(_issue, monkeypatch, "offer", "error")
    assert "after Add and device authentication" in str(exc)


def test_a_prompt_answered_inside_the_outcome_wait_counts(monkeypatch):
    """`wait_for_outcome` answers repeat prompts and records them; that is authentication too."""
    _no_prompt(monkeypatch, cf)

    def answers_then_errors(*a, **k):
        progress.step("answered device authentication (1)")
        return "error"

    monkeypatch.setattr(cf, "_wait_for_result", lambda *a, **k: "offer")
    monkeypatch.setattr(cf, "wait_for_outcome", answers_then_errors)
    with pytest.raises(outcome.FlowFailure) as exc:
        cf.run(Driver(), provider=SimpleNamespace(get=lambda n: URL),
               credential_name="credential_issuance", app_package="io.authbound.wallet",
               **PAGE_ARGS)
    assert "after Add and device authentication" in str(exc.value)


def test_a_prompt_answered_before_add_is_not_authentication_after_it(monkeypatch):
    _no_prompt(monkeypatch, cf)

    def unlocked_then_offer(*a, **k):
        progress.step("answered the biometric prompt")   # the app's own unlock, before the offer
        return "offer"

    monkeypatch.setattr(cf, "_wait_for_result", unlocked_then_offer)
    monkeypatch.setattr(cf, "wait_for_outcome", lambda *a, **k: "error")
    with pytest.raises(outcome.FlowFailure) as exc:
        cf.run(Driver(), provider=SimpleNamespace(get=lambda n: URL),
               credential_name="credential_issuance", app_package="io.authbound.wallet",
               **PAGE_ARGS)
    assert "no authentication recorded after it" in str(exc.value)


@pytest.mark.parametrize("prompt, says", [
    (True, "was accepted and authenticated but"),
    (False, "was accepted, with no authentication recorded after Add, but"),
])
def test_an_issuance_timeout_says_whether_it_authenticated(monkeypatch, prompt, says):
    if not prompt:
        _no_prompt(monkeypatch, cf)
    exc, _ = _raised(_issue, monkeypatch, "offer", "timeout")
    assert says in str(exc)


@pytest.mark.parametrize("prompt, says", [
    (True, "was shared and authenticated but"),
    (False, "was shared, with no authentication recorded after Share, but"),
])
def test_a_verification_timeout_says_whether_it_authenticated(monkeypatch, prompt, says):
    if not prompt:
        _no_prompt(monkeypatch, vf)
    exc, _ = _raised(_verify, monkeypatch, "request", "timeout")
    assert says in str(exc)


def test_a_prompt_answered_before_share_is_not_authentication_after_it(monkeypatch):
    """`_wait_for_request` answers the app's biometric prompt *before* the request screen."""
    _no_prompt(monkeypatch, vf)

    def unlocked_then_request(*a, **k):
        progress.step("answered the biometric prompt")
        return "request"

    monkeypatch.setattr(vf, "_wait_for_request", unlocked_then_request)
    monkeypatch.setattr(vf, "wait_for_outcome", lambda *a, **k: "timeout")
    with pytest.raises(outcome.FlowFailure) as exc:
        vf.run(Driver(), provider=SimpleNamespace(get=lambda n: URL),
               credential_name="pension_verification", app_package="io.authbound.wallet",
               **PAGE_ARGS)
    assert "no authentication recorded after Share" in str(exc.value)


def test_a_pin_typed_before_the_enrollment_wizard_counts_as_authentication(monkeypatch):
    """Android accepts the PIN and *then* pushes its enrollment offer, failing the auth call. The
    PIN was answered; saying "no authentication" there would be false."""
    def typed_then_wizard(*a, **k):
        raise RuntimeError("PIN was accepted but the system auth UI did not close")

    monkeypatch.setattr(cf, "authenticate_with_pin", typed_then_wizard)
    monkeypatch.setattr(cf, "in_biometric_enrollment", lambda *a, **k: True)
    exc, _ = _raised(_issue, monkeypatch, "offer", "error")
    assert "after Add and device authentication" in str(exc)


def test_only_a_step_that_starts_with_answered_counts(monkeypatch):
    """A later step that merely mentions the word must not turn into a claim of authentication."""
    _no_prompt(monkeypatch, cf)

    def mentions_it(*a, **k):
        progress.step("wallet error: the request was not answered in time")
        return "error"

    monkeypatch.setattr(cf, "_wait_for_result", lambda *a, **k: "offer")
    monkeypatch.setattr(cf, "wait_for_outcome", mentions_it)
    with pytest.raises(outcome.FlowFailure) as exc:
        cf.run(Driver(), provider=SimpleNamespace(get=lambda n: URL),
               credential_name="credential_issuance", app_package="io.authbound.wallet",
               **PAGE_ARGS)
    assert "no authentication recorded after it" in str(exc.value)
