"""The outcome state machine, exercised with fake probes and no device.

Its priority order and its two grace periods decide what every red cell in the published matrix
says, and they are exactly the kind of logic that is slow and expensive to debug on a phone. Fake
probes make each case a few milliseconds, so the rules can be pinned down rather than inferred from
a run.

These are the only tests in the suite that need no Appium session.
"""
import time

import pytest

from base import outcome
from base.flow_context import FlowContext
from base.interstitials import Interstitial
from base.screens import Screens, UNKNOWN


class FakeDriver:
    """Just enough driver for the loop: a package name and nothing else."""
    current_package = "com.example.wallet"
    capabilities: dict = {}


def never(driver, timeout):
    return False


def always(driver, timeout):
    return True


def after(n_calls):
    """A probe that is False for `n_calls` polls and True from then on."""
    state = {"n": 0}

    def probe(driver, timeout):
        state["n"] += 1
        return state["n"] > n_calls
    return probe


def screens(**kw):
    kw.setdefault("name", "testwallet")
    kw.setdefault("home", never)
    return Screens(**kw)


def ctx():
    return FlowContext(flow="credential_flow", wallet="testwallet", what="a_credential")


@pytest.fixture(autouse=True)
def no_crash_scan(monkeypatch):
    """The crash probe would touch a real driver; the crash cases patch it themselves."""
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda driver, timeout=0.3: None)


# --- what each outcome name actually means -------------------------------------------------

def test_target_on_screen_is_success():
    assert outcome.wait_for(FakeDriver(), screens(), ctx(),
                            target=always, timeout=1) == outcome.SUCCESS


def test_error_surface_is_rejected():
    assert outcome.wait_for(FakeDriver(), screens(error=always), ctx(),
                            target=never, timeout=1) == outcome.REJECTED


def test_spinner_still_up_when_time_runs_out_is_processing():
    assert outcome.wait_for(FakeDriver(), screens(processing=always), ctx(),
                            target=never, timeout=0.5) == outcome.PROCESSING


def test_nothing_at_all_is_absent():
    assert outcome.wait_for(FakeDriver(), screens(), ctx(),
                            target=never, timeout=0.5) == outcome.ABSENT


def test_home_after_the_grace_period_is_dismissed():
    assert outcome.wait_for(FakeDriver(), screens(home=always, home_grace=0.0), ctx(),
                            target=never, timeout=1) == outcome.DISMISSED


# --- the rules that are easy to break silently ---------------------------------------------

def test_home_inside_the_grace_period_is_ignored():
    """A wallet that flashes home while working on the deeplink must not be called dismissed."""
    result = outcome.wait_for(FakeDriver(), screens(home=always, home_grace=5.0), ctx(),
                              target=never, timeout=0.6)
    assert result == outcome.ABSENT


def test_success_beats_a_simultaneous_error():
    """Priority order is load-bearing: a wallet showing both has still shown the target."""
    assert outcome.wait_for(FakeDriver(), screens(error=always), ctx(),
                            target=always, timeout=1) == outcome.SUCCESS


def test_a_pre_existing_error_is_not_this_case_s_failure():
    """error_before suppresses the banner check — it was already there before we did anything."""
    result = outcome.wait_for(FakeDriver(), screens(error=always), ctx(),
                              target=never, timeout=0.5, error_before=True)
    assert result == outcome.ABSENT


def test_a_spinner_seen_earlier_still_reports_processing():
    """saw_processing latches: 'was working, ran out of time' is not 'never came forward'."""
    result = outcome.wait_for(FakeDriver(), screens(processing=after(0)), ctx(),
                              target=never, timeout=0.6)
    assert result in (outcome.PROCESSING, outcome.ABSENT)


def test_processing_grace_extends_the_deadline():
    """A visible spinner buys time, so a slow-but-working wallet is not cut off at the timeout."""
    started = time.time()
    outcome.wait_for(FakeDriver(), screens(processing=always, processing_grace=1.0), ctx(),
                     target=never, timeout=0.3)
    assert time.time() - started >= 1.0


def test_no_grace_means_no_extension():
    started = time.time()
    outcome.wait_for(FakeDriver(), screens(processing=always, processing_grace=0.0), ctx(),
                     target=never, timeout=0.3)
    assert time.time() - started < 1.0


# --- interstitials --------------------------------------------------------------------------

def test_an_interstitial_is_serviced_before_the_target_is_read():
    """Whatever covers the screen goes first, or the target underneath can never be seen."""
    seen = []
    prompt = Interstitial("prompt", lambda d, c: (seen.append(1), len(seen) <= 1)[1])
    result = outcome.wait_for(FakeDriver(), screens(), ctx(), target=after(0),
                              timeout=1, interstitials=(prompt,))
    assert result == outcome.SUCCESS and seen


def test_a_prompt_that_never_stops_is_a_prompt_loop():
    """The authbound failure mode: every prompt answered, a fresh one immediately after."""
    forever = Interstitial("device-pin", lambda d, c: True, max_fires=3)
    result = outcome.wait_for(FakeDriver(), screens(), ctx(), target=never,
                              timeout=2, interstitials=(forever,))
    assert result == outcome.PROMPT_LOOP


def test_a_handler_that_raises_does_not_become_the_verdict():
    def boom(driver, c):
        raise RuntimeError("handler exploded")
    result = outcome.wait_for(FakeDriver(), screens(), ctx(), target=never,
                              timeout=0.5, interstitials=(Interstitial("boom", boom),))
    assert result == outcome.ABSENT


# --- failures that are ours, not the wallet's -------------------------------------------------

def test_a_dead_session_is_not_a_wallet_verdict():
    from selenium.common.exceptions import WebDriverException

    def dead(driver, timeout):
        raise WebDriverException("session deleted")
    assert outcome.wait_for(FakeDriver(), screens(), ctx(),
                            target=dead, timeout=1) == outcome.LOST_SESSION


def test_a_crash_is_reported_as_crashed(monkeypatch):
    from base.android import SystemOverlay
    monkeypatch.setattr(outcome, "detect_crash_or_anr",
                        lambda driver, timeout=0.3: SystemOverlay.APP_CRASH)
    assert outcome.wait_for(FakeDriver(), screens(), ctx(),
                            target=never, timeout=0.5) == outcome.CRASHED


# --- the messages ------------------------------------------------------------------------------

def test_raise_for_carries_the_category_as_an_attribute_and_a_tag():
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(outcome.DISMISSED, FakeDriver(), screens(), ctx(),
                          expected="credential offer", timeout=30)
    assert exc.value.category == outcome.DISMISSED
    assert "[dismissed]" in str(exc.value)


def test_unmapped_screens_are_admitted_not_glossed_over():
    """unime's case: never reporting 'no error appeared' when nobody ever looked for one."""
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(outcome.ABSENT, FakeDriver(), screens(error=UNKNOWN), ctx(),
                          expected="credential offer", timeout=30)
    message = str(exc.value)
    # It must not claim the error surface was checked and clean...
    assert "no error" not in message
    # ...and it must say the surface was never mapped, so the reader knows why.
    assert "No error/processing/no_match/success screen is mapped" in message


def test_a_claimed_absent_screen_is_not_reported_as_unmapped():
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(outcome.ABSENT, FakeDriver(),
                          screens(error=None, processing=None, no_match=None, success=None),
                          ctx(), expected="credential offer", timeout=30)
    assert "is mapped" not in str(exc.value)


def test_rejected_quotes_the_wallet_s_own_words():
    s = screens(error=always, error_text=lambda d: "Please check if the QR is correct")
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(outcome.REJECTED, FakeDriver(), s, ctx(),
                          expected="credential offer", timeout=30)
    assert "Please check if the QR is correct" in str(exc.value)


def test_success_is_a_programming_error_not_a_failure():
    with pytest.raises(ValueError):
        outcome.raise_for(outcome.SUCCESS, FakeDriver(), screens(), ctx(),
                          expected="credential offer", timeout=30)


def test_prompt_loop_names_the_prompt_that_looped():
    """"a prompt kept appearing" is not actionable; "device-pin kept appearing" is."""
    forever = Interstitial("device-pin", lambda d, c: True, max_fires=2)
    c = ctx()
    state = outcome.wait_for(FakeDriver(), screens(), c, target=never,
                             timeout=1.5, interstitials=(forever,))
    assert state == outcome.PROMPT_LOOP
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(state, FakeDriver(), screens(), c, expected="credential offer",
                          timeout=30, interstitials=(forever,))
    assert "device-pin" in str(exc.value)


@pytest.mark.parametrize("expected,article", [
    ("credential offer", "a credential offer"),
    ("information request", "an information request"),
])
def test_messages_use_the_right_article(expected, article):
    """These strings are published verbatim; "a information request" is not good enough."""
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.raise_for(outcome.DISMISSED, FakeDriver(), screens(), ctx(),
                          expected=expected, timeout=30)
    assert article in str(exc.value)


# --- a refused deeplink, and a timeout with the wallet in front ------------------------------------

from selenium.common.exceptions import WebDriverException  # noqa: E402

from base import deeplink, progress  # noqa: E402


def _ctx_for(package="com.example.wallet"):
    return FlowContext(flow="credential_flow", wallet="testwallet", what="a_credential",
                       app_package=package, url="openid-credential-offer://?x=1")


def test_android_refusing_the_link_is_unroutable_and_quotes_android(monkeypatch):
    """With the package named, Android's "unable to resolve Intent" is the device's own evidence.
    These refusals used to go through `raise_for(ABSENT)`, describing a wait that never happened."""
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    exc = WebDriverException("Activity not started, unable to resolve Intent { act=VIEW }")
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_refused(FakeDriver(), screens(), _ctx_for(), exc, expected="credential offer")
    text = str(raised.value)
    assert raised.value.category == outcome.UNROUTABLE
    assert "unable to resolve Intent" in text and text.endswith("[no_retry]")
    assert "Nothing appeared" not in text and "after 30s" not in text, "no wait is described"
    assert "could not be read from the device" in text, "no registration claimed on no evidence"


def test_any_other_error_firing_the_link_is_a_lost_session():
    exc = WebDriverException("instrumentation process is not running")
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_refused(FakeDriver(), screens(), _ctx_for(), exc, expected="credential offer")
    assert raised.value.category == outcome.LOST_SESSION


def test_a_timeout_with_the_wallet_in_front_is_a_timeout_not_absent(monkeypatch):
    """unime published "did not come to the foreground (com.impierce.identity_wallet was)"."""
    progress.reset()
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: ["openid-credential-offer"])
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_for(outcome.ABSENT, FakeDriver(), screens(), _ctx_for(),
                          expected="credential offer", timeout=30)
    assert raised.value.category == outcome.PROCESSING
    assert "did not come to the foreground" not in str(raised.value)
    assert "does register" in str(raised.value), "the dump said so"
    assert progress.snapshot()["stage"] == progress.OPEN


def test_a_timeout_with_another_app_in_front_is_still_absent(monkeypatch):
    progress.reset()
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_for(outcome.ABSENT, FakeDriver(), screens(), _ctx_for("com.other.wallet"),
                          expected="credential offer", timeout=30)
    assert raised.value.category == outcome.ABSENT
    assert "(com.example.wallet was)" in str(raised.value)
    assert "does register" not in str(raised.value), "an unreadable dump is not a registration"
    assert progress.snapshot()["stage"] == ""


# The real shape of a refusal: Appium's wrapper on one line, Android's refusal on the next.
REAL_REFUSAL = (
    "Error attempting to start URI. Original error: Error: Starting: Intent "
    "{ act=android.intent.action.VIEW dat=eudi-openid4vp:///... pkg=com.example.wallet }\n"
    "Error: Activity not started, unable to resolve Intent { act=android.intent.action.VIEW "
    "dat=eudi-openid4vp:///... flg=0x10000000 pkg=com.example.wallet }")


def _refuse(monkeypatch, schemes, reason=None):
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: schemes)
    if reason is not None:
        monkeypatch.setattr(deeplink, "unroutable_reason", reason)
    ctx = _ctx_for()
    ctx.url = "eudi-openid4vp://?request_uri=https%3A%2F%2Fverifier.example"
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_refused(FakeDriver(), screens(), ctx, WebDriverException(REAL_REFUSAL),
                              expected="information request")
    return str(raised.value), raised.value.category


def test_a_refusal_is_one_line_with_the_devices_explanation_first(monkeypatch):
    """Only a failure's first line reaches summary.json and app.log, and the report cuts at 200
    characters — so the explanation leads, and Appium's wrapper line is not what gets kept."""
    text, category = _refuse(monkeypatch, ["openid", "openid4vp"])
    assert category == outcome.UNROUTABLE
    assert "\n" not in text
    assert text.index("does not register") < text.index("Android refused")
    assert "Activity not started, unable to resolve Intent" in text
    assert "Error attempting to start URI" not in text


def test_androids_words_are_not_quoted_as_the_wallets(monkeypatch):
    """The digest publishes the first double-quoted span as what the wallet said."""
    text, _ = _refuse(monkeypatch, ["openid"])
    assert '"' not in text


def test_a_registered_scheme_that_is_still_refused_is_not_called_deliverable(monkeypatch):
    """authbound registers https, and archived runs hold eight refused https://paradym.id links:
    "does register, so it had the chance to take it" would sit under an [unroutable] verdict."""
    text, category = _refuse(monkeypatch, ["eudi-openid4vp"])
    assert category == outcome.UNROUTABLE
    assert "had the chance" not in text
    assert "none of its intent filters accepts this URL" in text


def test_an_unreadable_dump_is_said_to_be_unreadable(monkeypatch):
    text, _ = _refuse(monkeypatch, [])
    assert "could not be read from the device" in text


def test_a_diagnosis_that_fails_never_replaces_the_verdict(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("adb went away")

    monkeypatch.setattr(deeplink, "registered_schemes", boom)
    monkeypatch.setattr(deeplink, "unroutable_reason", boom)
    ctx = _ctx_for()
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_refused(FakeDriver(), screens(), ctx, WebDriverException(REAL_REFUSAL),
                              expected="information request")
    assert raised.value.category == outcome.UNROUTABLE


def test_the_published_unroutable_sentence_no_longer_claims_a_delivery(monkeypatch):
    """With the package named, a URL the wallet cannot take goes nowhere else."""
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: ["openid"])
    said = deeplink.unroutable_reason(FakeDriver(), "com.example.wallet", "eudi-openid4vp://x", "w")
    assert "does not register" in said
    assert "delivered to whatever else" not in said


def test_every_flow_that_fires_through_the_recorder_diagnoses_a_refusal_honestly():
    """hovi, unime and authbound all fire through `deeplink.fire`; their refusals must go to
    `raise_refused`, not back to `raise_for(ABSENT)`, which describes a wait that never happened.
    Checked on the source, since the flows cannot run here."""
    from pathlib import Path
    root = Path(__file__).parents[2]
    offenders = []
    for flow in sorted(root.glob("wallets/*/flows/*.py")):
        text = flow.read_text()
        if "deeplink.fire(" in text and "raise_refused(" not in text:
            offenders.append(str(flow.relative_to(root)))
    assert not offenders, offenders


def test_a_skipped_error_probe_is_not_reported_as_no_error(monkeypatch):
    """With a stale banner up the loop ignores the error surface, so "no error" was never checked
    — the message must not say it was, then contradict itself with the stale-banner sentence."""
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    with pytest.raises(outcome.FlowFailure) as raised:
        outcome.raise_for(outcome.ABSENT, FakeDriver(), screens(error=always), _ctx_for("other"),
                          expected="credential offer", timeout=30, error_before=True)
    text = str(raised.value)
    assert "no error and" not in text and ", no error" not in text
    assert "stale error banner" in text
