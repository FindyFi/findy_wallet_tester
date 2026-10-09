"""Every way heidi's flows can end, mapped to the outcome it raises and how far it got.

heidi kept its own polling loops — they answer the connection-consent screen and the fingerprint
prompt in whatever order heidi raises them — and those loops are unchanged. What changed is what
each of their answers becomes. Before, every branch raised a bare RuntimeError and the report read
a reason back out of the sentence; heidi's connection refusal matched no pattern at all and was
published as a plain red cell, and a refused deeplink escaped as a raw driver exception.

The loops and page objects are replaced by fakes that return each possible answer; the flow code
between them is the real code. Nothing here touches a device.
"""
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from wallets.heidi.flows import credential_flow as cf, verification_flow as vf

URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example"
PACKAGE = "ch.ubique.heidi.android"

REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=openid4vp:///... pkg=ch.ubique.heidi.android }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=openid4vp:///... pkg=ch.ubique.heidi.android }")


class Driver:
    capabilities: dict = {}

    def __init__(self, refuse=None, front=PACKAGE):
        self.refuse = refuse
        self.current_package = front

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException(self.refuse)


class _Error:
    text = "An error occurred. | Inserted identity could not be parsed"

    def __init__(self, driver, **kw):
        pass

    def get_error_text(self):
        return self.text


class _Connection:
    def __init__(self, driver, **kw):
        pass

    def refusal_detail(self):
        return "issuer.example"

    def connect(self):
        pass


class _Tap:
    def __init__(self, driver, **kw):
        pass

    def accept(self):
        pass

    def share(self):
        pass

    def dismiss(self):
        pass


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    progress.reset()
    for m in (cf, vf):
        monkeypatch.setattr(m, "ErrorPage", _Error)
        monkeypatch.setattr(m, "ConnectionPage", _Connection)
    monkeypatch.setattr(cf, "CredentialOfferPage", _Tap)
    monkeypatch.setattr(vf, "VerificationRequestPage", _Tap)
    monkeypatch.setattr(vf, "VerificationSuccessPage", _Tap)
    monkeypatch.setattr(vf, "_no_match_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(vf, "_home_on_screen", lambda d, timeout=2: False)
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield
    progress.reset()


PAGE_ARGS = {"timeouts": {"credential_offer": 1}}


def _issue(monkeypatch, first, driver=None):
    monkeypatch.setattr(cf, "_wait_for_result", lambda *a, **k: first)
    cf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="credential_issuance", app_package=PACKAGE, **PAGE_ARGS)


def _verify(monkeypatch, first, after="shared", driver=None):
    monkeypatch.setattr(vf, "_wait_for_request", lambda *a, **k: first)
    monkeypatch.setattr(vf, "_wait_for_share_result", lambda *a, **k: after)
    vf.run(driver or Driver(), provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package=PACKAGE, **PAGE_ARGS)


def _raised(fn, *args, **kwargs):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args, **kwargs)
    return exc.value, progress.snapshot()


# --- issuance -----------------------------------------------------------------------------------

@pytest.mark.parametrize("first, category, stage", [
    ("error", outcome.REJECTED, progress.OPEN),
    ("refused", outcome.REJECTED, progress.OPEN),
    ("home", outcome.DISMISSED, progress.OPEN),
    ("timeout", outcome.PROCESSING, progress.OPEN),     # heidi is in front: see below
])
def test_every_issuance_ending_is_a_named_outcome_at_the_stage_it_reached(
        monkeypatch, first, category, stage):
    exc, trail = _raised(_issue, monkeypatch, first)
    assert exc.category == category
    assert trail["stage"] == stage


def test_a_connection_refusal_is_a_rejection_not_a_plain_red_cell(monkeypatch):
    """No prose pattern matched "refused to connect", so the report published nothing."""
    exc, _ = _raised(_issue, monkeypatch, "refused")
    assert exc.category == outcome.REJECTED
    assert '"A connection cannot be established."' in str(exc), "heidi's own words, for the digest"


def test_an_accepted_offer_reaches_present_and_leaves_keep_to_the_count(monkeypatch):
    """heidi shows no confirmation after Add; the test's count is the evidence."""
    _issue(monkeypatch, "offer")
    snap = progress.snapshot()
    assert snap["stage"] == progress.PRESENT
    assert snap["steps"][-1].endswith("tapped Add")


def test_a_timeout_with_something_else_in_front_is_absent(monkeypatch):
    exc, trail = _raised(_issue, monkeypatch, "timeout", driver=Driver(front="com.android.systemui"))
    assert exc.category == outcome.ABSENT
    assert trail["stage"] == progress.DELIVER


@pytest.mark.parametrize("run", ["issue", "verify"])
def test_a_refused_deeplink_is_unroutable_not_a_raw_driver_exception(monkeypatch, run):
    """It used to escape from `mobile: deepLink` uncaught."""
    if run == "issue":
        exc, trail = _raised(_issue, monkeypatch, "offer", driver=Driver(refuse=REFUSED))
    else:
        exc, trail = _raised(_verify, monkeypatch, "request", driver=Driver(refuse=REFUSED))
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == ""


def test_a_dead_session_in_the_issuance_wait_is_ours(monkeypatch):
    def dead(*a, **k):
        raise WebDriverException("instrumentation process is not running")

    monkeypatch.setattr(cf, "handle_biometric_if_present", dead)
    exc, _ = _raised(cf.run, Driver(), provider=SimpleNamespace(get=lambda n: URL),
                     credential_name="c", app_package=PACKAGE, **PAGE_ARGS)
    assert exc.category == outcome.LOST_SESSION


def test_the_error_copy_is_quoted_and_a_placeholder_is_not(monkeypatch):
    exc, _ = _raised(_issue, monkeypatch, "error")
    assert '"An error occurred. | Inserted identity could not be parsed"' in str(exc)

    class Unreadable(_Error):
        text = "<could not read error text>"

    monkeypatch.setattr(cf, "ErrorPage", Unreadable)
    progress.reset()
    exc, _ = _raised(_issue, monkeypatch, "error")
    assert '"<could not read error text>"' not in str(exc) and '"' not in str(exc)
    assert "could not read error text" in str(exc)


# --- verification -------------------------------------------------------------------------------

@pytest.mark.parametrize("first, after, category, stage", [
    ("error", None, outcome.REJECTED, progress.OPEN),
    ("refused", None, outcome.REJECTED, progress.OPEN),
    ("request", "error", outcome.REJECTED, progress.MATCH),
    ("request", "timeout", outcome.PROCESSING, progress.MATCH),
])
def test_every_verification_ending_is_a_named_outcome_at_the_stage_it_reached(
        monkeypatch, first, after, category, stage):
    exc, trail = _raised(_verify, monkeypatch, first, after)
    assert exc.category == category
    assert trail["stage"] == stage


def test_a_request_never_shown_while_heidi_sits_on_its_dashboard_is_dismissed(monkeypatch):
    """This wait never looks for home, so the shared diagnosis would say "no home screen" about a
    wallet that was showing it the whole time."""
    monkeypatch.setattr(vf, "_home_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_verify, monkeypatch, "timeout")
    assert exc.category == outcome.DISMISSED
    assert "no home screen" not in str(exc)
    assert trail["stage"] == progress.OPEN


def test_a_request_never_shown_and_home_gone_goes_to_the_shared_diagnosis(monkeypatch):
    exc, _ = _raised(_verify, monkeypatch, "timeout", driver=Driver(front="com.android.systemui"))
    assert exc.category == outcome.ABSENT


def test_no_matching_credentials_is_no_match(monkeypatch):
    """The empty-wallet case is decided by the test before the flow runs (`nothing_to_present`)."""
    monkeypatch.setattr(vf, "_no_match_on_screen", lambda d, timeout=2: True)
    exc, trail = _raised(_verify, monkeypatch, "request")
    assert exc.category == outcome.NO_MATCH
    assert '"No Matching Credentials"' in str(exc)
    assert trail["stage"] == progress.PRESENT


def test_a_confirmed_share_completes_accept(monkeypatch):
    _verify(monkeypatch, "request", "shared")
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_home_without_a_confirmation_passes_but_claims_nothing(monkeypatch):
    """Unchanged: it passes, as it always has. It is not evidence the share was handed over."""
    _verify(monkeypatch, "request", "home")
    snap = progress.snapshot()
    assert snap["stage"] == progress.MATCH
    assert "without confirming the share" in snap["steps"][-1]


@pytest.mark.parametrize("loop", ["_wait_for_request", "_wait_for_share_result"])
def test_a_dead_session_in_either_verification_wait_is_ours(monkeypatch, loop):
    def dead(*a, **k):
        raise WebDriverException("instrumentation process is not running")

    monkeypatch.setattr(vf, "handle_biometric_if_present", dead)
    if loop == "_wait_for_request":
        exc, _ = _raised(vf.run, Driver(), provider=SimpleNamespace(get=lambda n: URL),
                         credential_name="v", app_package=PACKAGE, **PAGE_ARGS)
    else:
        monkeypatch.setattr(vf, "_wait_for_request", lambda *a, **k: "request")
        exc, _ = _raised(vf.run, Driver(), provider=SimpleNamespace(get=lambda n: URL),
                         credential_name="v", app_package=PACKAGE, **PAGE_ARGS)
    assert exc.category == outcome.LOST_SESSION


def test_heidis_flows_raise_no_bare_runtime_errors():
    from pathlib import Path
    root = Path(__file__).parents[2] / "wallets" / "heidi" / "flows"
    for name in ("credential_flow.py", "verification_flow.py"):
        assert "raise RuntimeError(" not in (root / name).read_text(), name


# --- the probes and readers screens.py hands to the shared code -------------------------------

def test_heidi_declares_its_error_screen_rather_than_leaving_it_unknown():
    """Declared, the digest reads heidi's copy and the diagnosis may say "no error"; left
    UNKNOWN, both would have to admit nobody looked."""
    from wallets.heidi.pages import error_page
    from wallets.heidi.screens import SCREENS
    assert SCREENS.probe("error") is error_page.any_error
    assert SCREENS.error_text is error_page.message
    assert SCREENS.unmapped() == ["processing"], "only the spinner is unobserved"


@pytest.mark.parametrize("which", ["issuance", "verification"])
def test_either_of_heidis_two_error_screens_counts(monkeypatch, which):
    from wallets.heidi.pages import error_page
    showing = error_page.SCREEN_ID if which == "issuance" else error_page.VERIFICATION_SCREEN_ID
    monkeypatch.setattr(error_page, "wait_present", lambda d, loc, timeout=2: loc == showing)
    assert error_page.any_error(object())


@pytest.mark.parametrize("raw, said", [
    ("An error occurred. | Inserted identity could not be parsed",
     "An error occurred. | Inserted identity could not be parsed"),
    ("<could not read error text>", ""),
    ("", ""),
])
def test_message_passes_on_only_heidis_own_words(monkeypatch, raw, said):
    from wallets.heidi.pages import error_page
    monkeypatch.setattr(error_page, "any_error", lambda d, timeout=2: True)
    monkeypatch.setattr(error_page.ErrorPage, "get_error_text", lambda self: raw)
    assert error_page.message(object()) == said


def test_an_empty_error_text_is_not_quoted():
    from wallets.heidi.pages.error_page import quoted
    assert quoted("") == " (no error text)"
    assert '"' not in quoted("<could not read error text>")
    assert quoted("Boom") == ': "Boom"'


def test_the_dialogs_cancel_button_is_not_part_of_what_heidi_said():
    from wallets.heidi.pages.error_page import ErrorPage

    class El:
        def __init__(self, text):
            self.text = text

        def get_attribute(self, name):
            return self.text

    driver = SimpleNamespace(find_elements=lambda *a: [El("An error occurred."), El("Nope!"),
                                                        El("CANCEL")])
    assert ErrorPage(driver).get_error_text() == "An error occurred. | Nope!"


def test_a_request_never_shown_with_heidi_in_front_off_its_dashboard_is_a_timeout(monkeypatch):
    """Neither on its dashboard nor anywhere recognised: the shared diagnosis's in-front branch."""
    exc, trail = _raised(_verify, monkeypatch, "timeout")
    assert exc.category == outcome.PROCESSING
    assert "heidi was in front" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_the_consent_screen_proves_heidi_came_forward(monkeypatch):
    """heidi's own screen; a wallet that then handed off must not read as never having come up."""
    shown = iter([True, False])

    monkeypatch.setattr(cf, "handle_biometric_if_present", lambda *a, **k: False)
    monkeypatch.setattr(cf, "wait_present",
                        lambda d, loc, timeout=1: loc == cf._connection_id and next(shown, False))
    exc, trail = _raised(cf.run, Driver(front="com.android.chrome"),
                         provider=SimpleNamespace(get=lambda n: URL),
                         credential_name="c", app_package=PACKAGE, **PAGE_ARGS)
    assert trail["stage"] == progress.OPEN
    assert "accepted the connection consent" in trail["steps"][-1]
