"""Shared helpers for wallet conftest fixtures.

These utilities extract the boilerplate that every wallet's ``_ensure_home``
fixture needs: navigating to the home screen (with optional session reset),
capturing failure artifacts, and running teardown.  Import them in each
wallet conftest to avoid repeating the same ~40 lines.
"""
import importlib
import json
import logging
import os
import re
from datetime import datetime, timezone

# pytest's own report classes, so a redacted traceback still prints like one and keeps the crash
# message the short summary line shows. Private API: if a pytest release moves them, the
# traceback is still redacted, as plain text (see `redact_report`), rather than the run failing.
try:
    from _pytest._code.code import ReprFileLocation, TerminalRepr
except ImportError:  # pragma: no cover - depends on the pytest release
    ReprFileLocation = TerminalRepr = None

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from base import outcome
from base.android import SystemOverlay, detect_crash_or_anr, is_secure_screen_refusal
from base.flow_failures import lost_session
from base.outcome import FlowFailure
from base.redact import drop_unlock_exchanges, in_secret_window, redact
from base.utils import sanitize_test_name

logger = logging.getLogger(__name__)


def _typed_init_failure(exc: Exception, during: str, driver=None) -> FlowFailure:
    """`exc` as a FlowFailure, so a failed reset or navigation never reaches the report as a bare
    exception (which publishes a whole wallet column as an uncounted-reason "Error").

    A FlowFailure passes through unchanged. A WebDriverException that is not a plain wait or
    lookup miss is a dead session: `lost_session`, which is ours and not the wallet's. Before
    anything else is called `setup`, Android's crash/ANR dialog is looked for once: a wallet that
    crashed is `crashed`, not an uncounted setup problem. Anything else (a RuntimeError from a
    wallet's init_flow, a TimeoutException, a missing or stale element) is `setup`: the wallet
    could not be put in the state the tests need. A plain exception's text is kept verbatim, so
    reports of older runs and the report's patterns still match; a wallet's own error screen
    during init still reads `setup` unless that wallet's init_flow types it (as gataca does).
    """
    if isinstance(exc, FlowFailure):
        return exc
    said = " ".join(str(getattr(exc, "msg", None) or exc).split())
    if isinstance(exc, WebDriverException) and not isinstance(
        exc, (TimeoutException, NoSuchElementException, StaleElementReferenceException)
    ):
        return lost_session("init_flow", during, said)
    original = str(exc) or type(exc).__name__
    try:
        overlay = detect_crash_or_anr(driver)
    except WebDriverException:
        return lost_session("init_flow", during, said)
    except Exception:
        overlay = None
    if overlay is not None:
        anr = overlay is SystemOverlay.ANR
        return FlowFailure(outcome.CRASHED, (
            f"[init_flow] The wallet {'stopped responding' if anr else 'crashed'} {during}: "
            f"Android showed its {'not-responding' if anr else 'crash'} dialog. {original}"
        ))
    first_line = " ".join(
        (str(getattr(exc, "msg", None) or exc).strip().splitlines() or [""])[0].split())
    if isinstance(exc, TimeoutException):
        return FlowFailure(outcome.SETUP, f"[init_flow] Timed out {during}: {first_line[:200]}")
    if isinstance(exc, (NoSuchElementException, StaleElementReferenceException)):
        return FlowFailure(outcome.SETUP, (
            f"[init_flow] A screen element was not where it was expected {during}: "
            f"{first_line[:200]}"))
    return FlowFailure(outcome.SETUP, original)


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
        # A reset that has already burned its reruns is not attempted again.
        #
        # The flag below is set only on success, so a reset that always fails used to be retried
        # by every test in the wallet: 11 tests x 3 attempts x ~2 min sent hovi's 2026-09-09
        # segment to 84 minutes of wall clock for zero signal, all 10 cells reporting the same
        # error. The first test still gets its full rerun budget — a wipe-and-onboard can be
        # genuinely flaky — but once *that* node has given up, every later test fails in
        # milliseconds with the original cause instead of re-running the wipe from scratch.
        owner = getattr(request.config, "_session_reset_owner", None)
        failure = getattr(request.config, "_session_reset_failure", None)
        if failure is not None and owner != request.node.nodeid:
            raise FlowFailure(
                getattr(request.config, "_session_reset_failure_category", outcome.SETUP),
                "Session reset already failed for this wallet earlier in the run — not retrying. "
                f"First failure: {failure} [no_retry]",
            )

        request.config._session_reset_owner = request.node.nodeid
        logger.info("[conftest] skip_if_done=false — resetting wallet for this session")
        try:
            init_flow.run(
                app.driver,
                pin=pin,
                app_package=app_package,
                skip_if_done=False,
                **app.page_args,
            )
        except Exception as e:
            typed = _typed_init_failure(e, "while resetting the wallet", app.driver)
            request.config._session_reset_failure = f"{type(e).__name__}: {e}"
            request.config._session_reset_failure_category = typed.category
            if typed is e:
                raise
            raise typed from e
        request.config._session_reset_done = True
    else:
        try:
            init_flow.run(
                app.driver,
                pin=pin,
                app_package=app_package,
                skip_if_done=True,
                **app.page_args,
            )
        except Exception as e:
            typed = _typed_init_failure(e, "while returning to the home screen", app.driver)
            if typed is e:
                raise
            raise typed from e


def node_failed(node) -> bool:
    """True when the test failed in its body **or** blew up in fixture setup.

    Checking only ``rep_call`` misses the failure mode that most needs evidence. When a fixture
    raises, pytest records an **Error** and ``rep_call`` never exists, so a wallet that dies in
    setup publishes a column of red cells with no screenshot and no XML dump anywhere — which is
    how toppan's post-wipe language screen went unrecorded across 62 historical dumps and had to
    be described by hand.

    Both attributes are set by the root conftest's ``pytest_runtest_makereport`` wrapper, and
    finalizers run in the teardown phase, after the setup report exists.
    """
    for phase in ("setup", "call"):
        report = getattr(node, f"rep_{phase}", None)
        if report is not None and report.failed:
            return True
    return False


def wallet_of(request) -> str:
    """The wallet under test, from the parametrised `driver` fixture — the config carries no name."""
    try:
        return request.node.callspec.params["driver"]
    except Exception:
        return ""


def mark_screen_protected(request, wallet: str) -> None:
    """Record that this wallet refused a capture because its screen sets FLAG_SECURE.

    Kept on the pytest config so it survives across tests in the run, and on the node so the
    report can show it per test. It is a wallet **property**, not a failure — nothing here fails
    a test, by decision (2026-09-04).
    """
    protected = getattr(request.config, "_protected_wallets", None)
    if protected is None:
        protected = set()
        request.config._protected_wallets = protected
    protected.add(wallet)
    request.node.user_properties.append(("screen_protected", wallet))


def screen_is_protected(request, wallet: str) -> bool:
    """True if this wallet has refused a capture at any point in this run."""
    return wallet in getattr(request.config, "_protected_wallets", set())


_QUOTED = re.compile(r'"([^"]{1,400})"')
# A quoted span that is a locator, not something a wallet said. Selenium puts the failing locator
# in its own message — `The element 'By.xpath: //*[@resource-id="com.android.systemui:id/lockPassword"]'`
# — and the inner double quotes match `_QUOTED` perfectly. hovi's 2026-09-18 run published
# `wallet said: "com.android.systemui:id/lockPassword"` six times before this existed. The
# double-quote convention holds for messages this repo formats; it says nothing about the text of
# exceptions raised inside the driver.
# `^//` and `^\(//`, not a bare `//`: an unanchored `//` matches the scheme separator of any URL,
# so it threw away wallet copy that merely quoted one. Measured over the digest's own inputs
# (app.log + test.log across every run in reports/), anchoring recovers 171 spans and every one
# of them is genuine copy — 159 URLs plus authbound's "Unable to retrieve access token from ...",
# procivis's "Fetching well known metadata from ..." and walt.id's "Invalid client_id prefix..." —
# while a quoted XPath, a parenthesised one and a bare `pkg:id/name` all stay suppressed.
_LOCATORISH = re.compile(r'@resource-id|By\.\w+|^//|^\(//|^[\w.]+:id/[\w.]+$')
# The content test above cannot see a span whose words are plain but whose context is a locator.
# base/base_page.py formats `Element {locator} not clickable after {t}s` with the Python tuple repr,
# so reports/2026-11-05_17-08-31/paradym/app.log published `wallet said: "Yes, archive"` from
# `Element ('xpath', '//*[@content-desc="Yes, archive"]')`, and `wallet said: "true"` five times
# from `[@clickable="true" and ...]`. Two context rules close that: the span lies inside a
# `('strategy', 'value')` repr (single or double quoted, with backslash escapes, as repr picks
# the quote by content), or it is an XPath predicate value (`@attr=` or `contains(@attr, `).
# The strategy is matched against the real `By`/`AppiumBy` names, not any word: a message that
# formats a tuple of the wallet's copy, `('Error', "Can't connect")`, would otherwise lose it.
_Q = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
_STRATEGY = (r"(?:xpath|id|name|class name|css selector|link text|partial link text|tag name"
             r"|accessibility id|-[a-z][a-z ]*)")
_LOCATOR_TUPLE = re.compile(r"""\(\s*(['"])""" + _STRATEGY + r"""\1\s*,\s*""" + _Q + r"\s*\)")
_PREDICATE_BEFORE = re.compile(r"@[\w:.-]+\s*[=,]\s*$")
_LEADING_TAG = re.compile(r"\s*\[([^\]]+)\]")
# "TimeoutException: Message:" — an exception type and nothing else.
_NO_MESSAGE = re.compile(r"^(?:Message:?)?\s*$")


def _wallet_words(line: str) -> str:
    """The first quoted span of a failure line that is the wallet's words, or "".

    A span is rejected when its content looks like a locator (`_LOCATORISH`) or when its context
    is one: inside a locator tuple repr, or the value of an XPath attribute predicate.
    """
    tuples = [m.span() for m in _LOCATOR_TUPLE.finditer(line)]
    for m in _QUOTED.finditer(line):
        if _LOCATORISH.search(m.group(1)):
            continue
        if any(a <= m.start() < b for a, b in tuples):
            continue
        if _PREDICATE_BEFORE.search(line[:m.start()]):
            continue
        return m.group(1)
    return ""


def _error_line(line: str, where: str) -> str:
    """The failure, with its location when the exception itself says nothing.

    Selenium raises `TimeoutException` with an empty message and puts everything in the
    stacktrace, so the one-line form is a bare type name. procivis's first digested failure read
    `TimeoutException: Message:` and carried no information at all; the file and line it was
    raised at is then the only thing that answers "where did this hang?".
    """
    rest = line.split(":", 1)[1] if ":" in line else ""
    if _NO_MESSAGE.match(rest.strip()):
        kind = line.split(":", 1)[0]
        return f"{kind} — no message; raised at {where}" if where else f"{kind} — no message"
    return f"{line}  (at {where})" if where else line


def _leading_tags(line: str) -> list:
    """The bracket tags at the front of a failure message, in order.

    ``"FlowFailure: [rejected] [credential_flow] hovi accepted ..."`` -> ``["rejected",
    "credential_flow"]``. Only the run of tags at the start is read, so a bracket appearing later
    in the wallet's own error copy is not mistaken for a flow name.
    """
    rest = line.split(":", 1)[1] if ":" in line else line
    tags = []
    while True:
        match = _LEADING_TAG.match(rest)
        if not match:
            return tags
        tags.append(match.group(1))
        rest = rest[match.end():]


def _reached_line(trail) -> str:
    """How far the flow got, from `base/progress.py`'s snapshot: the stage, then the steps.

    "No completed step recorded" rather than "got nowhere": most wallets' flows do not record
    progress yet, and an empty trail from them says nothing about how far they got.
    """
    if not trail or not trail.get("steps"):
        return "(no completed step recorded)"
    stage = trail.get("stage") or "no stage completed"
    return f"{stage} — {' → '.join(trail['steps'])}"


def _failed_phase(node) -> str:
    """"setup" or "call" — which phase produced the failure. "" if the node did not fail."""
    for phase in ("setup", "call"):
        report = getattr(node, f"rep_{phase}", None)
        if report is not None and report.failed:
            return phase
    return ""


def _error_surface(wallet: str) -> str:
    """What this wallet has declared about its error screen, in three states.

    `base/screens.py` keeps "no such screen" (a claim) apart from "nobody looked" (UNKNOWN), and
    the digest has to preserve that distinction or it republishes ignorance as fact: writing
    "no error screen appeared" for a wallet whose error surface has never been mapped is a lie.
    Read through the public `Screens` API only, so a wallet that renames a probe fails here the
    same way it fails everywhere else.
    """
    try:
        screens = importlib.import_module(f"wallets.{wallet}.screens").SCREENS
    except Exception:
        return "unmapped"
    if "error" in screens.unmapped():
        return "unmapped"
    return "mapped" if screens.probe("error") else "none"


def _read_error_text(driver, wallet: str) -> str:
    """The wallet's own error copy, read live. "" when it cannot be read. Never raises."""
    try:
        screens = importlib.import_module(f"wallets.{wallet}.screens").SCREENS
    except Exception:
        return ""
    reader = screens.error_text
    if not reader:
        return ""
    try:
        return (reader(driver) or "").strip()
    except Exception:
        return ""


def record_error_screen(driver, request, config, *, wallet: str = "", evidence=()) -> None:
    """Append one block to app.log saying what the wallet showed when this test failed.

    app.log is the run's error-screen digest: for every failed test, one block naming what was
    tested against, where in the flow it broke, and what the wallet itself said — so a run can be
    read without opening twenty screenshots. It is deliberately *not* a log stream; the device's
    logcat lives in logcat.log and the Appium server's in appium.log.

    Everything here is already known at this point, which is the whole reason it is cheap: the
    provider and case come from the test's own parameters, the flow and outcome category from the
    exception (`base.outcome.FlowFailure` carries `.category` as an attribute for exactly this
    kind of use), and the wallet's error copy is normally already quoted inside the message,
    because `raise_if_rejected` read it at the only moment it was reliably on screen. No wallet
    needs to add anything, and no extra UI interaction happens on a screen a test just failed on
    — which is what made paradym's in-app log scrape both fragile and destructive.

    Writes one block per *attempt* — `_error_screen_logged` keeps a single attempt to one block,
    and the root conftest clears it on each setup so a rerun gets its own. Never raises: a second
    exception here would mask the failure this is supposed to describe.
    """
    if getattr(request.node, "_error_screen_logged", False):
        return
    request.node._error_screen_logged = True
    # One block per *attempt*, not per test: the earlier blocks are the earlier causes, which a
    # retried failure often does not share. When a rerun starts, `archive_previous_attempt` renames
    # the finished attempt's files and re-points its block's `evidence:` line at them.
    attempt = getattr(request.node, "_attempt", 1)

    try:
        line = getattr(request.node, "_failure_line", "") or "(no exception recorded)"
        category = getattr(request.node, "_failure_category", "")

        # The message is prefixed with its own tags: "[rejected] [credential_flow] ...". The
        # category is taken from the attribute, never parsed. Of what remains, a `*_flow` tag names
        # the flow and anything else names the phase the wallet was in — paradym and toppan tag
        # "[after consent]", "[after PIN]", "[after confirmation]", which the digest used to
        # publish under `flow:`. Both are worth reporting; calling one the other is not.
        tags = [t for t in _leading_tags(line) if t != category]
        flow = next((t for t in tags if t.endswith("_flow")), "")
        phase = next((t for t in tags if t != flow), "")

        # Prefer the copy captured at failure time over a live re-read: by teardown the surface may
        # be gone (hovi's error is a banner, suspected to be a timed toast).
        # The first quoted span that is not a locator: a message can carry both, and a wallet's
        # own words are worth more than the first match.
        said = _wallet_words(line)
        said = said or _read_error_text(driver, wallet)

        surface = _error_surface(wallet)
        if said:
            said_line = f'"{said}"'
        elif surface == "none":
            said_line = f"(nothing — {wallet or 'this wallet'} declares no error screen)"
        elif surface == "unmapped":
            said_line = f"(unknown — no error screen is mapped for {wallet or 'this wallet'})"
        else:
            said_line = "(nothing read from the mapped error screen)"

        params = getattr(getattr(request.node, "callspec", None), "params", {}) or {}
        against = ", ".join(f"{k}={v}" for k, v in params.items() if k != "driver")

        app = config.get("application", {})
        version = _wallet_version(request)
        build = f" {version}" if version else ""

        block = [
            f"\n--- TEST: {sanitize_test_name(request.node.name)} ---",
            f"when:           {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            + (f"   (attempt {attempt})" if attempt > 1 else ""),
            f"wallet:         {wallet or '?'}{build} ({app.get('package', '?')})",
            f"tested against: {against or '(no parameters)'}",
            f"failed in:      {_failed_phase(request.node) or '?'}",
            f"flow:           {flow or '(not reported by the failure)'}",
            *([f"phase:          {phase}"] if phase else []),
            f"outcome:        {category or '(uncategorised)'}",
            f"reached:        {_reached_line(getattr(request.node, '_failure_trail', None))}",
            f"wallet said:    {said_line}",
            f"error:          {_error_line(line, getattr(request.node, '_failure_where', ''))}",
            f"evidence:       {', '.join(evidence) if evidence else '(none saved)'}",
        ]
        with (request.config._run_dir / "app.log").open("a", encoding="utf-8") as f:
            f.write(redact("\n".join(block)) + "\n")
    except Exception as e:
        logger.warning(f"[conftest] Could not record the error screen digest: {e}")


def _wallet_version(request) -> str:
    """The build under test, from the app_info.json this run already wrote. "" if unavailable."""
    try:
        info = json.loads((request.config._run_dir / "app_info.json").read_text())
        return f"v{info.get('version_name', '?')} (build {info.get('version_code', '?')})"
    except Exception:
        return ""


def note_attempt_file(node, rel: str) -> None:
    """Remember a run-dir-relative file this attempt wrote, for `archive_previous_attempt`."""
    try:
        files = getattr(node, "_attempt_files", None)
        if files is None:
            files = node._attempt_files = []
        if rel not in files:
            files.append(rel)
    except Exception as e:
        logger.warning(f"[conftest] Could not note {rel} as attempt evidence: {e}")


def _with_attempt(rel: str, attempt: int) -> str:
    """`xml_dumps/t.xml` -> `xml_dumps/t.attempt2.xml`."""
    stem, ext = os.path.splitext(rel)
    return f"{stem}.attempt{attempt}{ext}"


def _repoint_block(log: str, header: str, attempt: int, renames: dict):
    """`log` with the last `header` block's `evidence:` entries swapped per `renames`.

    None when there is nothing to change: no such block, a block of another attempt, or no
    entry of the evidence line is in `renames`.
    """
    lines = log.split("\n")
    starts = [i for i, ln in enumerate(lines) if ln == header]
    if not starts:
        return None
    first = starts[-1]
    end = next((i for i in range(first + 1, len(lines)) if lines[i].startswith("--- TEST: ")),
               len(lines))
    when = next((ln for ln in lines[first:end] if ln.startswith("when:")), "")
    found = re.search(r"\(attempt (\d+)\)\s*$", when)
    if (int(found.group(1)) if found else 1) != attempt:
        return None
    for i in range(first, end):
        if lines[i].startswith("evidence:"):
            head, _, tail = lines[i].partition(":")
            pad = tail[:len(tail) - len(tail.lstrip())]
            entries = [e.strip() for e in tail.split(",")]
            swapped = [renames.get(e, e) for e in entries]
            if swapped == entries:
                return None
            lines[i] = f"{head}:{pad}{', '.join(swapped)}"
            return "\n".join(lines)
    return None


def archive_previous_attempt(item) -> None:
    """Keep the evidence of an attempt that is being rerun, under `.attempt<N>` names.

    pytest-rerunfailures re-runs the same item, and every attempt writes the same filenames, so
    attempts 1 and 2 used to point at attempt 3's files from their app.log blocks. An attempt
    cannot know at capture time whether a rerun follows, so its files are renamed when one
    actually starts; the final attempt keeps the plain names and a run without reruns is
    unchanged. Called first in the root conftest's `pytest_runtest_setup`, which runs for every
    test, so the no-op path is one getattr.

    The app.log rewrite is a read-modify-write. It is safe because only this process appends to
    this wallet's app.log between attempts; two xdist workers sharing one run dir could
    interleave, and this repo documents xdist as unsupported for the run's reports (see
    `write_summary`). Never raises: it runs in setup, and a failure here must not fail the test.
    """
    files = getattr(item, "_attempt_files", None)
    if not files:
        return
    try:
        attempt = item._attempt
        run_dir = item.config._run_dir
        name = sanitize_test_name(item.name)
        renames = {}
        for rel in files:
            src = run_dir / rel
            if src.exists():
                new = _with_attempt(rel, attempt)
                os.replace(src, run_dir / new)
                renames[rel] = new
        if not renames:
            return
        app_log = run_dir / "app.log"
        if app_log.exists():
            updated = _repoint_block(app_log.read_text(encoding="utf-8"),
                                     f"--- TEST: {name} ---", attempt, renames)
            if updated is not None:
                tmp = app_log.with_name("app.log.tmp")
                tmp.write_text(updated, encoding="utf-8")
                os.replace(tmp, app_log)
        logger.info(f"[conftest] Rerun of {name}: kept attempt {attempt}'s evidence as "
                    f"{', '.join(renames.values())}")
    except Exception as e:
        logger.warning(f"[conftest] Could not archive the previous attempt's evidence: {e}")
    finally:
        item._attempt_files = []


def save_failure_artifacts(driver, request, config, *, wallet: str = "") -> bool:
    """Dump the page XML and try a screenshot for a failed test. Returns True if anything saved.

    **XML first, always.** On a screen with FLAG_SECURE the screenshot cannot be taken at all, so
    the dump is the only evidence that survives — it must not be contingent on the screenshot, nor
    ordered after it. (The two copies of this logic that this function replaces disagreed on that
    order.)

    A secure-screen refusal is logged as one line and recorded as a wallet property. Previously it
    surfaced as a generic "Could not save screenshot" plus the full Java stacktrace — 363 lines of
    it per heidi run — indistinguishable from the screenshot machinery breaking.
    """
    reporting = config.get("reporting", {})
    test_name = sanitize_test_name(request.node.name)
    saved = False
    evidence = []

    if reporting.get("xml_on_failure", True):
        try:
            xml_dir = request.config._run_dir / "xml_dumps"
            xml_dir.mkdir(parents=True, exist_ok=True)
            path = xml_dir / f"{test_name}.xml"
            path.write_text(redact(driver.page_source), encoding="utf-8")
            logger.info(f"[conftest] XML dump saved: {path}")
            evidence.append(f"xml_dumps/{path.name}")
            note_attempt_file(request.node, evidence[-1])
            saved = True
        except Exception as e:
            logger.warning(f"[conftest] Could not save XML dump: {e}")

    if reporting.get("screenshot_on_failure", True):
        try:
            screenshot_dir = request.config._run_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = screenshot_dir / f"{test_name}.png"
            driver.save_screenshot(str(path))
            logger.info(f"[conftest] Screenshot saved: {path}")
            evidence.append(f"screenshots/{path.name}")
            note_attempt_file(request.node, evidence[-1])
            saved = True
        except Exception as e:
            if is_secure_screen_refusal(e):
                logger.info(
                    f"[conftest] No screenshot: {wallet or 'this wallet'} sets FLAG_SECURE on "
                    "this screen. The XML dump is the evidence for this test"
                )
                if wallet:
                    mark_screen_protected(request, wallet)
            else:
                logger.warning(f"[conftest] Could not save screenshot: {e}")

    # Last, so the digest can name the files that actually got written. Called from here rather
    # than from the two callers, because both of them reach this function and only this function
    # knows what was saved — one call site, so a failure cannot be digested twice or missed.
    record_error_screen(driver, request, config, wallet=wallet, evidence=evidence)

    return saved


def capture_failure_artifact(app, request):
    """Save failure artifacts for a failed test, once.

    Sets ``request.node._artifact_captured`` so the root conftest's ``app`` fixture doesn't
    attempt a second capture.
    """
    if not node_failed(request.node) or getattr(request.node, "_artifact_captured", False):
        return

    if save_failure_artifacts(app.driver, request, app.config, wallet=wallet_of(request)):
        request.node._artifact_captured = True


def _server_log_disabled(exc):
    """True when ``exc`` is Appium refusing ``get_log('server')`` because the operator did not
    start it with ``--allow-insecure=*:get_server_logs``."""
    text = getattr(exc, "msg", None) or str(exc)
    return "get_server_logs" in text and "has not been enabled" in text


def _appium_server(driver):
    """The address of the Appium server ``driver`` talks to, or "" when it cannot be told.

    Never raises: it is only used to name the server in a log line, and teardown must not fail
    over a label. ``command_executor.client_config`` is selenium's public accessor for the
    ClientConfig the connection was built from.
    """
    try:
        return str(driver.command_executor.client_config.remote_server_addr or "")
    except Exception:
        return ""


def capture_appium_logs(driver, request, test_name):
    """Append this test's share of the Appium server log to appium.log, once each.

    ``driver.get_log('server')`` does **not** drain the buffer, which this function assumed for
    months. It returns the most recent ~3000 entries every time, so consecutive per-test blocks
    overlapped heavily: measured on authbound's 2026-09-10 run, adjacent blocks shared 42-77% of
    their lines and the file as a whole was 108,781 lines carrying 31,009 distinct ones — 71%
    duplication, and 22 MB of it. Worse than the size, every block was mislabelled: entries from
    earlier tests sat under the name of a later one, so the file could not be used as evidence
    about the test whose header it appeared under.

    The Appium server also outlives the suite — the operator starts it by hand and leaves it up —
    so the very first block of a run used to open with whatever was still in the buffer from
    previous runs. authbound's first block spanned 10:06:29 to 11:11:30 for a session that began
    at 11:11:18: an hour of a different run, filed under `test_app_launch`.

    Both are fixed by advancing a cursor instead of trusting the buffer: an entry is written only
    if it is newer than the last one written *and* not older than this wallet's session. The
    cursor lives on the pytest config, so it is per wallet — `run_tests.py` calls `pytest.main()`
    in one process for the whole fleet, and a module-level cursor would leak one wallet's position
    into the next.

    One limit is inherent and worth knowing: the 3000-entry ceiling is the server's, so a test
    that produces more than 3000 entries between two calls loses the oldest of them. Nothing here
    can recover those — only running the Appium server with its own ``--log`` file could.

    Timestamps (ms since epoch) are formatted to match ``test.log`` (``YYYY-MM-DD HH:MM:SS``).

    An Appium server started without ``--allow-insecure=*:get_server_logs`` refuses every
    ``get_log('server')``, and selenium's message for it carries a ~25-line Node stacktrace:
    reports/2026-11-05_17-08-31/paradym/test.log held it 15 times, once after each attempt (13
    tests, 2 reruns), because the run's AVD-lane server was started without the flag. The refusal
    is a property of the server, not of the test, so it is logged once per server with the fix and
    the fetch is skipped afterwards. The set of refusing servers lives on the pytest config for the
    same reason as the cursor: each wallet's ``pytest.main()`` has its own ``test.log``. Any
    other error is still logged in full, every time.
    """
    config = request.config
    server = _appium_server(driver)
    refused = getattr(config, "_appium_log_refused", None)
    if refused is None:
        refused = config._appium_log_refused = set()
    if server in refused:
        return
    try:
        entries = driver.get_log("server")
    except Exception as e:
        if _server_log_disabled(e):
            refused.add(server)
            logger.warning(
                f"[conftest] No appium.log for this run: the Appium server "
                f"{f'at {server}' if server else 'for this session'} has 'get_server_logs' "
                "disabled. Start Appium with --allow-insecure=*:get_server_logs to capture it"
            )
        else:
            logger.warning(f"[conftest] Could not fetch Appium logs: {e}")
        return
    if not entries:
        return

    session_start = getattr(config, "_session_started_at", 0)
    last_ts, seen_at_last = getattr(config, "_appium_cursor", (session_start, frozenset()))

    def _fresh(entry):
        ts = entry.get("timestamp", 0)
        if ts < last_ts:
            return False
        # Same millisecond as the cursor: only entries not already written.
        return ts > last_ts or entry.get("message", "") not in seen_at_last

    fresh = [e for e in entries if _fresh(e)]

    # A clock the server and this process disagree on would otherwise silence the log forever, and
    # silence is the failure mode this whole change exists to end. Keep everything the first time
    # and say so, rather than writing an empty file for the rest of the run.
    if not fresh and not hasattr(config, "_appium_cursor"):
        logger.warning(
            "[conftest] Every Appium entry looks older than this session — the Appium server's "
            "clock may differ from this machine's. Capturing them anyway"
        )
        fresh = list(entries)

    if not fresh:
        return

    newest = max(e.get("timestamp", 0) for e in fresh)
    config._appium_cursor = (
        newest,
        frozenset(e.get("message", "") for e in fresh if e.get("timestamp", 0) == newest),
    )

    lines = []
    typed = 0
    for entry in fresh:
        ts_ms = entry.get("timestamp", 0)
        # PIN entry on a wallet's keypad: the element lookups name each digit (base/redact.py).
        if in_secret_window(ts_ms):
            typed += 1
            continue
        if typed:
            lines.append(f"[redacted] {typed} Appium log lines of PIN entry")
            typed = 0
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone()
        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        level = entry.get("level", "INFO").upper()
        lines.append(f"{ts_str} [{level}] {entry.get('message', '')}")
    if typed:
        lines.append(f"[redacted] {typed} Appium log lines of PIN entry")

    # The device unlock is left out whole (its keypad clicks spell the PIN), then every line loses
    # PINs and local paths: Appium logs typed values and its own install paths.
    appium_log = config._run_dir / "appium.log"
    with appium_log.open("a", encoding="utf-8") as f:
        f.write(f"\n--- TEST: {test_name} ---\n")
        for line in drop_unlock_exchanges(lines):
            f.write(redact(line) + "\n")


def teardown_test(app, request, init_flow):
    """Capture any failure artifact then navigate back to the home screen.

    Call this at the end of an ``_ensure_home`` fixture (after ``yield``) as the
    standard teardown for wallets that don't need extra post-test steps.
    """
    capture_failure_artifact(app, request)
    capture_appium_logs(app.driver, request, sanitize_test_name(request.node.name))
    # Tells the `app` fixture's fallback that this test's Appium block is already written, so it
    # does not drain the buffer a second time and append a near-empty duplicate.
    request.node._appium_captured = True

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


class _RedactedRepr(TerminalRepr or object):
    """A failure's traceback, already rendered and redacted, that prints like the original.

    Keeps `reprcrash`, which pytest's short summary line ("FAILED x - <message>") reads.
    """

    def __init__(self, text: str, reprcrash):
        self.text = text
        self.reprcrash = reprcrash

    def toterminal(self, tw) -> None:
        for line in self.text.split("\n"):
            tw.line(line)


def redact_report(rep) -> None:
    """Remove PINs and local paths from a test or collection report, in place. Never raises.

    A failure's traceback prints each frame's arguments, and a PIN passed as one used to appear in
    pytest.out, report.html and the nightly log. A skip keeps its (path, lineno, reason) tuple,
    which pytest and `_skip_reason` both rely on; only its text is redacted.
    """
    try:
        longrepr = getattr(rep, "longrepr", None)
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            path, lineno, reason = longrepr
            rep.longrepr = (redact(str(path)), lineno, redact(str(reason)))
        elif longrepr is not None and rep.failed and TerminalRepr is None:
            rep.longrepr = redact(rep.longreprtext)
        elif longrepr is not None and rep.failed:
            crash = getattr(longrepr, "reprcrash", None)
            if crash is not None:
                crash = ReprFileLocation(redact(str(crash.path)), crash.lineno,
                                         redact(str(crash.message)))
            # `longreprtext` renders without colour codes even when FORCE_COLOR is set; the report
            # generator reads report.html's "E   ..." lines and colour would hide them from it.
            rep.longrepr = _RedactedRepr(redact(rep.longreprtext), crash)
        rep.sections = [(title, redact(content)) for title, content in rep.sections]
    except Exception as e:
        logger.warning(f"[conftest] Could not redact a report: {type(e).__name__}")


def redact_terminal(config) -> None:
    """Redact everything pytest writes to the terminal from now on. Once per session; never raises.

    The test reports are already redacted, but pytest and its plugins print other lines with full
    paths: the header (python, rootdir), the warnings summary, and pytest-html's closing
    "Generated html report: file:///Users/...". The terminal is pytest.out under the parallel
    runner and the nightly log under run_tests.py.
    """
    try:
        writer = config.get_terminal_writer()
        if getattr(writer, "_redacting", False):
            return
        write = writer.write

        def redacting_write(msg, **markup):
            return write(redact(msg), **markup)

        writer.write = redacting_write
        writer._redacting = True
    except Exception as e:
        logger.warning(f"[conftest] Could not redact the terminal output: {type(e).__name__}")
