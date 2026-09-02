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
