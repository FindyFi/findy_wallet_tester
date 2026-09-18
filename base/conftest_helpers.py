"""Shared helpers for wallet conftest fixtures.

These utilities extract the boilerplate that every wallet's ``_ensure_home``
fixture needs: navigating to the home screen (with optional session reset),
capturing failure artifacts, and running teardown.  Import them in each
wallet conftest to avoid repeating the same ~40 lines.
"""
import importlib
import json
import logging
import re
from datetime import datetime, timezone

from base.android import is_secure_screen_refusal
from base.utils import sanitize_test_name

logger = logging.getLogger(__name__)


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
            raise RuntimeError(
                "Session reset already failed for this wallet earlier in the run — not retrying. "
                f"First failure: {failure}"
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
            request.config._session_reset_failure = f"{type(e).__name__}: {e}"
            raise
        request.config._session_reset_done = True
    else:
        init_flow.run(
            app.driver,
            pin=pin,
            app_package=app_package,
            skip_if_done=True,
            **app.page_args,
        )


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
_LEADING_TAG = re.compile(r"\s*\[([^\]]+)\]")
# "TimeoutException: Message:" — an exception type and nothing else.
_NO_MESSAGE = re.compile(r"^(?:Message:?)?\s*$")


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
    # One block per *attempt*, not per test. A rerun overwrites the screenshot and XML dump, so
    # the last block is the one whose `evidence:` files are still on disk; the earlier blocks are
    # the earlier causes, which a retried failure often does not share.
    attempt = getattr(request.node, "_attempt", 1)

    try:
        line = getattr(request.node, "_failure_line", "") or "(no exception recorded)"
        category = getattr(request.node, "_failure_category", "")

        # The message is prefixed with its own tags: "[rejected] [credential_flow] ...". The
        # category is taken from the attribute, never parsed; the remaining tag names the flow.
        flow = next((t for t in _leading_tags(line) if t != category), "")

        # Prefer the copy captured at failure time over a live re-read: by teardown the surface may
        # be gone (hovi's error is a banner, suspected to be a timed toast).
        quoted = _QUOTED.search(line)
        said = quoted.group(1) if quoted else _read_error_text(driver, wallet)

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
            f"outcome:        {category or '(uncategorised)'}",
            f"wallet said:    {said_line}",
            f"error:          {_error_line(line, getattr(request.node, '_failure_where', ''))}",
            f"evidence:       {', '.join(evidence) if evidence else '(none saved)'}",
        ]
        with (request.config._run_dir / "app.log").open("a", encoding="utf-8") as f:
            f.write("\n".join(block) + "\n")
    except Exception as e:
        logger.warning(f"[conftest] Could not record the error screen digest: {e}")


def _wallet_version(request) -> str:
    """The build under test, from the app_info.json this run already wrote. "" if unavailable."""
    try:
        info = json.loads((request.config._run_dir / "app_info.json").read_text())
        return f"v{info.get('version_name', '?')} (build {info.get('version_code', '?')})"
    except Exception:
        return ""


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
            path.write_text(driver.page_source, encoding="utf-8")
            logger.info(f"[conftest] XML dump saved: {path}")
            evidence.append(f"xml_dumps/{path.name}")
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
    """
    config = request.config
    try:
        entries = driver.get_log("server")
    except Exception as e:
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

    appium_log = config._run_dir / "appium.log"
    with appium_log.open("a", encoding="utf-8") as f:
        f.write(f"\n--- TEST: {test_name} ---\n")
        for entry in fresh:
            ts_ms = entry.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone()
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            level = entry.get("level", "INFO").upper()
            message = entry.get("message", "")
            f.write(f"{ts_str} [{level}] {message}\n")


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
