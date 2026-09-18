"""app.log answers one question per failed test: what did the wallet show, and against what?

The file used to be an unfiltered device logcat under a name that described neither the device nor
the app — and it was empty in seven of eight wallets anyway. It now holds one block per failure,
built only from what is already known at the shared capture point: the test's own parameters name
the provider, the exception names the flow and carries its outcome category as an attribute, and
the wallet's own error copy is normally already quoted inside the message because
`raise_if_rejected` read it at the one moment it was reliably on screen.

Two properties matter more than the formatting, and both are regressions waiting to happen:

  * **It cannot lie about the error surface.** `base/screens.py` keeps "this wallet has no error
    screen" (a claim) apart from "nobody has looked" (UNKNOWN) precisely because unime's error
    surface was assumed absent for months and then found in 26 of its own dumps. A digest that
    printed "no error screen appeared" for an unmapped wallet would republish that mistake in a
    file people read as evidence.

  * **It cannot mask the failure it describes.** It runs inside the teardown of an already-failed
    test, so an exception raised here would replace a real wallet failure with a harness one.
"""
from types import SimpleNamespace

import pytest

from base.screens import Screens
from base.conftest_helpers import (
    _leading_tags,
    record_error_screen,
    save_failure_artifacts,
)

PKG = "droidwallet.hovi.id"


class Driver:
    page_source = "<hierarchy />"

    def save_screenshot(self, path):
        return True


class Exploding:
    """Everything the digest might touch on a dead session raises."""

    @property
    def page_source(self):
        raise Exception("A session is either terminated or not started")

    def save_screenshot(self, path):
        raise Exception("A session is either terminated or not started")


def _request(tmp_path, *, name="test_credential_issuance_hovi_issuer_credential_issuance",
             params=None, line="", category="", phase="call"):
    node = SimpleNamespace(name=name, nodeid=f"x::{name}")
    if params is not None:
        node.callspec = SimpleNamespace(params=params)
    if line:
        node._failure_line = line
        node._failure_category = category
    setattr(node, f"rep_{phase}", SimpleNamespace(failed=True))
    return SimpleNamespace(config=SimpleNamespace(_run_dir=tmp_path), node=node)


def _config():
    return {"application": {"package": PKG, "pin": "123456"}}


def _digest(tmp_path):
    return (tmp_path / "app.log").read_text()


def _field(body, label):
    for line in body.splitlines():
        if line.startswith(label):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no {label!r} line in:\n{body}")


def test_a_rejection_is_recorded_with_provider_flow_and_the_wallets_own_words(tmp_path):
    request = _request(
        tmp_path,
        params={"driver": "hovi", "issuer_name": "paradym_issuer", "test_case": "credential_issuance"},
        line=('FlowFailure: [rejected] [credential_flow] hovi accepted the offer for '
              '\'credential_issuance\' and then failed: "Something went wrong". The step completed'),
        category="rejected",
    )
    record_error_screen(Driver(), request, _config(), wallet="hovi",
                        evidence=["xml_dumps/x.xml", "screenshots/x.png"])
    body = _digest(tmp_path)

    # What it was tested against — the provider and case, not just the wallet.
    assert _field(body, "tested against") == \
        "issuer_name=paradym_issuer, test_case=credential_issuance"
    assert "driver=" not in body, "the driver param is the wallet, already on its own line"
    # Where in the flow, and what kind of outcome.
    assert _field(body, "flow") == "credential_flow"
    assert _field(body, "outcome") == "rejected"
    assert _field(body, "failed in") == "call"
    # What it read.
    assert _field(body, "wallet said") == '"Something went wrong"'
    assert _field(body, "wallet") == f"hovi ({PKG})"
    assert _field(body, "evidence") == "xml_dumps/x.xml, screenshots/x.png"
    assert body.count("--- TEST:") == 1


def test_the_outcome_category_comes_from_the_exception_not_from_parsing_prose(tmp_path):
    """`FlowFailure.category` is an attribute by design; a bracket in the copy is not a flow name."""
    request = _request(
        tmp_path,
        params={"driver": "hovi"},
        line=('FlowFailure: [rejected] [verification_flow] hovi refused and said '
              '"[E_1032] no matching credential"'),
        category="rejected",
    )
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    body = _digest(tmp_path)

    assert _field(body, "flow") == "verification_flow"
    assert _field(body, "outcome") == "rejected"
    assert _field(body, "wallet said") == '"[E_1032] no matching credential"'


def test_leading_tags_stop_at_the_first_non_tag():
    assert _leading_tags("FlowFailure: [rejected] [credential_flow] hovi said [not a tag]") == \
        ["rejected", "credential_flow"]
    assert _leading_tags("RuntimeError: [init_flow] Landing page not found after reset") == \
        ["init_flow"]
    assert _leading_tags("RuntimeError: nothing tagged at all") == []


def test_a_setup_failure_is_recorded_because_that_is_the_case_with_no_other_evidence(tmp_path):
    """hovi's 2026-09-09 run was every test dying in setup — 11 error cells, nothing to read."""
    request = _request(
        tmp_path,
        params={"driver": "hovi"},
        line="RuntimeError: [init_flow] Landing page not found after reset",
        phase="setup",
    )
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    body = _digest(tmp_path)

    assert _field(body, "failed in") == "setup"
    assert _field(body, "flow") == "init_flow"
    assert _field(body, "outcome") == "(uncategorised)"


def test_every_failed_test_gets_its_own_block_and_no_test_gets_two(tmp_path):
    """Both capture paths reach `save_failure_artifacts`; the second must be a no-op."""
    request = _request(tmp_path, params={"driver": "hovi"}, line="RuntimeError: boom")
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    record_error_screen(Driver(), request, _config(), wallet="hovi")

    other = _request(tmp_path, name="test_onboarding_hovi", params={"driver": "hovi"},
                     line="RuntimeError: [init_flow] no landing page")
    record_error_screen(Driver(), other, _config(), wallet="hovi")

    body = _digest(tmp_path)
    assert body.count("--- TEST:") == 2
    assert "test_credential_issuance_hovi_issuer_credential_issuance" in body
    assert "test_onboarding_hovi" in body


# --- the three states of "what did the wallet say" ------------------------------------------

def _with_screens(monkeypatch, screens):
    monkeypatch.setattr("base.conftest_helpers.importlib.import_module",
                        lambda name: SimpleNamespace(SCREENS=screens))


def test_a_wallet_that_claims_no_error_screen_is_reported_as_showing_nothing(tmp_path, monkeypatch):
    _with_screens(monkeypatch, Screens(name="w", home=lambda d, t=2: True, error=None))
    request = _request(tmp_path, params={"driver": "w"}, line="RuntimeError: [flow] timed out")
    record_error_screen(Driver(), request, _config(), wallet="w")

    assert _field(_digest(tmp_path), "wallet said") == "(nothing — w declares no error screen)"


def test_an_unmapped_error_surface_is_reported_as_unknown_never_as_clean(tmp_path, monkeypatch):
    """The distinction unime cost us: not looking is not the same as nothing being there."""
    _with_screens(monkeypatch, Screens(name="w", home=lambda d, t=2: True))  # error left UNKNOWN
    request = _request(tmp_path, params={"driver": "w"}, line="RuntimeError: [flow] timed out")
    record_error_screen(Driver(), request, _config(), wallet="w")

    said = _field(_digest(tmp_path), "wallet said")
    assert said == "(unknown — no error screen is mapped for w)"
    assert "no error" not in said.replace("no error screen is mapped", "")


def test_a_wallet_with_no_screens_module_at_all_is_unmapped_not_clean(tmp_path):
    """Six of the eight wallets have not adopted the contract yet — they must not read as clean."""
    request = _request(tmp_path, params={"driver": "toppan"}, line="RuntimeError: [flow] timed out")
    record_error_screen(Driver(), request, _config(), wallet="toppan")

    assert _field(_digest(tmp_path), "wallet said") == \
        "(unknown — no error screen is mapped for toppan)"


def test_a_mapped_surface_that_reads_nothing_says_so(tmp_path, monkeypatch):
    _with_screens(monkeypatch, Screens(
        name="w", home=lambda d, t=2: True,
        error=lambda d, t=2: True, error_text=lambda d: "",
    ))
    request = _request(tmp_path, params={"driver": "w"}, line="RuntimeError: [flow] timed out")
    record_error_screen(Driver(), request, _config(), wallet="w")

    assert _field(_digest(tmp_path), "wallet said") == "(nothing read from the mapped error screen)"


def test_the_live_read_is_used_when_the_message_quotes_nothing(tmp_path, monkeypatch):
    """Wallets that raise plain RuntimeErrors still get their copy recorded, if they expose it."""
    _with_screens(monkeypatch, Screens(
        name="w", home=lambda d, t=2: True,
        error=lambda d, t=2: True, error_text=lambda d: "Unable to process request",
    ))
    request = _request(tmp_path, params={"driver": "w"},
                       line="RuntimeError: [credential_flow] offer failed")
    record_error_screen(Driver(), request, _config(), wallet="w")

    assert _field(_digest(tmp_path), "wallet said") == '"Unable to process request"'


def test_a_reader_that_raises_does_not_take_the_digest_with_it(tmp_path, monkeypatch):
    def _boom(driver):
        raise Exception("stale element reference")

    _with_screens(monkeypatch, Screens(
        name="w", home=lambda d, t=2: True, error=lambda d, t=2: True, error_text=_boom,
    ))
    request = _request(tmp_path, params={"driver": "w"}, line="RuntimeError: [flow] x")
    record_error_screen(Exploding(), request, _config(), wallet="w")

    assert _field(_digest(tmp_path), "wallet said") == "(nothing read from the mapped error screen)"


# --- it must never raise into a teardown that is already handling a failure ------------------

def test_a_dead_session_still_produces_a_block(tmp_path):
    request = _request(tmp_path, params={"driver": "hovi"},
                       line="WebDriverException: session deleted")
    record_error_screen(Exploding(), request, _config(), wallet="hovi")
    assert "--- TEST:" in _digest(tmp_path)


@pytest.mark.parametrize("break_it", [
    pytest.param(lambda r: setattr(r.config, "_run_dir", None), id="no_run_dir"),
    pytest.param(lambda r: delattr(r.node, "callspec"), id="no_callspec"),
    pytest.param(lambda r: delattr(r.node, "rep_call"), id="no_report"),
], )
def test_nothing_here_can_mask_the_real_failure(tmp_path, break_it):
    request = _request(tmp_path, params={"driver": "hovi"}, line="RuntimeError: boom")
    break_it(request)
    record_error_screen(Exploding(), request, _config(), wallet="hovi")  # must not raise


def test_an_unparametrised_test_is_recorded_without_pretending_to_know_the_provider(tmp_path):
    request = _request(tmp_path, name="test_app_launch_hovi", line="RuntimeError: boom")
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    assert _field(_digest(tmp_path), "tested against") == "(no parameters)"


# --- wiring -----------------------------------------------------------------------------------

def test_save_failure_artifacts_writes_the_digest_and_names_what_it_saved(tmp_path):
    """One call site, so a failure cannot be digested twice or missed by one of the two paths."""
    request = _request(tmp_path, params={"driver": "hovi", "issuer_name": "waltid_issuer"},
                       line="RuntimeError: [credential_flow] boom")
    saved = save_failure_artifacts(Driver(), request, _config(), wallet="hovi")

    assert saved is True
    body = _digest(tmp_path)
    evidence = _field(body, "evidence")
    assert "xml_dumps/" in evidence and "screenshots/" in evidence
    assert _field(body, "tested against") == "issuer_name=waltid_issuer"


def test_the_digest_records_the_build_under_test_when_the_run_knows_it(tmp_path):
    (tmp_path / "app_info.json").write_text(
        '{"version_name": "1.0.7", "version_code": "1000702"}')
    request = _request(tmp_path, params={"driver": "hovi"}, line="RuntimeError: boom")
    record_error_screen(Driver(), request, _config(), wallet="hovi")

    assert _field(_digest(tmp_path), "wallet") == f"hovi v1.0.7 (build 1000702) ({PKG})"


# --- an exception with no message of its own -------------------------------------------------

def test_a_message_less_exception_is_reported_with_where_it_was_raised(tmp_path):
    """procivis's first real digested failure read `TimeoutException: Message:` and said nothing.

    Selenium raises `TimeoutException` with an empty message and puts the detail in a stacktrace,
    and a `WebDriverWait` without `message=` is the common case in this suite — so this is the
    normal shape of a timeout, not an edge case.
    """
    request = _request(tmp_path, params={"driver": "procivis"},
                       line="TimeoutException: Message:")
    request.node._failure_where = "wallets/procivis/flows/init_flow.py:83"
    record_error_screen(Driver(), request, _config(), wallet="procivis")

    err = _field(_digest(tmp_path), "error")
    assert err == "TimeoutException — no message; raised at wallets/procivis/flows/init_flow.py:83"
    assert "Message:" not in err, "the empty 'Message:' stub is noise, not information"


def test_a_real_message_keeps_its_text_and_gains_the_location(tmp_path):
    request = _request(tmp_path, params={"driver": "hovi"},
                       line="RuntimeError: [init_flow] Landing page not found after reset")
    request.node._failure_where = "wallets/hovi/flows/init_flow.py:191"
    record_error_screen(Driver(), request, _config(), wallet="hovi")

    err = _field(_digest(tmp_path), "error")
    assert "[init_flow] Landing page not found after reset" in err
    assert "(at wallets/hovi/flows/init_flow.py:191)" in err


def test_a_message_less_exception_with_no_location_still_names_the_type(tmp_path):
    request = _request(tmp_path, params={"driver": "hovi"}, line="TimeoutException: Message:")
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    assert _field(_digest(tmp_path), "error") == "TimeoutException — no message"


def test_a_rerun_gets_its_own_block_stamped_with_the_attempt(tmp_path):
    """A retried failure often fails differently, and the files on disk are the last attempt's.

    pytest-rerunfailures re-runs the *same* item, so the failure attributes have to be cleared per
    attempt — otherwise the block describes attempt 1 while the screenshot it points at is
    attempt 3's. The attempt number is what lets a reader tell which block matches the files.
    """
    first = _request(tmp_path, params={"driver": "hovi"},
                     line="RuntimeError: [credential_flow] first cause")
    record_error_screen(Driver(), first, _config(), wallet="hovi", evidence=["screenshots/x.png"])

    second = _request(tmp_path, params={"driver": "hovi"},
                      line="RuntimeError: [credential_flow] a different cause")
    second.node._attempt = 2
    record_error_screen(Driver(), second, _config(), wallet="hovi", evidence=["screenshots/x.png"])

    body = _digest(tmp_path)
    assert body.count("--- TEST:") == 2
    assert "(attempt 2)" in body
    assert body.index("first cause") < body.index("a different cause"), \
        "the last block must be the last attempt — it is the one whose evidence is on disk"


def test_a_first_attempt_is_not_cluttered_with_an_attempt_number(tmp_path):
    request = _request(tmp_path, params={"driver": "hovi"}, line="RuntimeError: boom")
    record_error_screen(Driver(), request, _config(), wallet="hovi")
    assert "attempt" not in _digest(tmp_path)
