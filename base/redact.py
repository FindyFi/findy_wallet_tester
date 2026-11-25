"""Keep PINs and this machine's paths out of everything a run writes.

Run records are read by people who should not learn the phone's PIN from them, and they travel:
the nightly log, a pull request, the published report. Two kinds of text are removed:

- **PINs.** The device PIN (`DEVICE_PIN`, `<WALLET>_DEVICE_PIN`) and each wallet's app PIN
  (`<WALLET>_APP_PIN`, the `pin` key in a wallet's config). They reached the records through
  pytest tracebacks (`page_args = {..., 'device_pin': ...}`), through Appium's server log (the
  `mobile: unlock` body, `setValue` bodies), and could through any log line.
- **Full local paths.** `/Users/<name>/...` says who ran it and where. Paths inside this repository
  become relative ("reports/<run>/..."), any other home folder becomes "~".

Two ways a PIN is recognised, because a record can hold one we were never told about (an old run,
another clone's `.env`):

- By value: every PIN the config loads is registered here. A quoted literal that is exactly a PIN
  ("key":"<PIN>", 'device_pin': '<PIN>', a setValue body) is masked whatever its length. A PIN of
  6 characters or more is also masked anywhere it stands alone. A shorter one is not: replacing
  "5554" everywhere would hit the emulator's serial and an Appium port, and "emulator-****" would
  give the PIN away instead of hiding it.
- By shape: `<something>_pin = '...'`, `"pin": "..."`, `DEVICE_PIN=...`, and Appium's
  "unlock device using pin ...".

`Secret` is the other half: the config hands PINs out as a `str` subclass whose repr is the mask,
so a traceback that prints function arguments shows `'****'` before any redaction has to run.

Standard library only: imported by the root conftest, the report generator and the tests alike.
"""

import logging
import os
import re
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent

MASK = "****"

# Shorter values are never registered: a one-character test PIN would mask every "p" in a log.
MIN_SECRET_LEN = 4
# Values at least this long are masked wherever they stand alone, not only when quoted.
FREE_TEXT_MIN_LEN = 6


class Secret(str):
    """A PIN. Types, iterates, compares and JSON-encodes as its plain value; its repr is the mask.

    `str(secret)`, slicing, `.strip()` and f-strings give plain `str` back, so code that types the
    PIN is unaffected, and so is anything that copies it — which is why `redact` still matches by
    value. A copied PIN shorter than `FREE_TEXT_MIN_LEN`, written into prose without quotes, is the
    one form nothing here catches: never log a PIN, not even a copied one.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return repr(MASK)


_secrets: set = set()

# Environment variables that hold a PIN. `DEVICE_PIN`, `<WALLET>_DEVICE_PIN`, `<WALLET>_APP_PIN`.
_SECRET_ENV = re.compile(r"(?:^|_)(?:DEVICE|APP)_PIN$")
# Config keys that hold a PIN: `pin`, `device_pin`, `app_pin`, ...
_SECRET_KEY = re.compile(r"(?i)^(?:\w+_)?pin$")


def register_secret(value) -> None:
    """Remember a PIN so `redact` masks it. Blank and very short values are ignored."""
    if isinstance(value, str) and len(value) >= MIN_SECRET_LEN:
        _secrets.add(str(value))


def clear_secrets() -> None:
    """Forget every registered PIN. For tests; a run only ever adds."""
    _secrets.clear()


def register_environment_secrets(environ=None) -> None:
    """Register every PIN the environment (`.env`, already loaded) defines."""
    environ = os.environ if environ is None else environ
    for name, value in environ.items():
        if _SECRET_ENV.search(name):
            register_secret(value)


def mark_secrets(config):
    """Wrap and register every PIN in a loaded config, in place. Returns the same object.

    A PIN is a non-empty string under a key named `pin` or ending in `_pin`, at any depth.
    """
    if isinstance(config, dict):
        for key, value in config.items():
            if isinstance(value, str) and value and _SECRET_KEY.match(str(key)):
                register_secret(value)
                config[key] = value if isinstance(value, Secret) else Secret(value)
            else:
                mark_secrets(value)
    elif isinstance(config, list):
        for item in config:
            mark_secrets(item)
    return config


# --- recognising a PIN by its shape ------------------------------------------------------------

# `device_pin = '1234'`, `'device_pin': '1234'`, `"pin": "1234"`. The name must *be* a pin name, so
# `mapping=`, `typing=` and `spinner=` are left alone.
_PIN_QUOTED = re.compile(
    r"""(?i)(\b(?:\w+_)?pin(?:_code)?\b['"]?\s*[:=]\s*)(['"])((?:(?!\2)[^\\\n]|\\.)+)\2""")
# `DEVICE_PIN=1234`, `pin: 1234` — unquoted. Only a value of 4 or more characters with a digit in
# it: source lines in a traceback (`pin: str`, `pin=pin`) name the variable rather than hold it, and
# a wallet's own "Incorrect PIN: 2 attempts remaining" must reach the report as the wallet said it.
_PIN_BARE = re.compile(
    r"""(?i)(\b(?:\w+_)?pin(?:_code)?\b\s*[:=]\s*)(?!['"\s])(?=[^\s,;'"\]})]{4,})"""
    r"""(?=[^\s,;'"\]})]*\d)([^\s,;'"\]})]+)""")
# Appium: `{"script":"mobile: unlock","args":[{"key":"1234",...` on one line.
_UNLOCK_KEY = re.compile(r'(mobile: unlock[^\n]*?"key"\s*:\s*")([^"]*)(")')
# Appium: "Trying to unlock device using pin 1234".
_UNLOCK_SAID = re.compile(r"(?i)(unlock(?:ing)?\s+(?:the\s+)?device\s+using\s+pin\s+)(\S+)")
# A JSON or Python string literal; compared whole against the registered PINs.
_QUOTED = re.compile(r"""(["'])((?:(?!\1)[^\\\n]|\\.){%d,64})\1""" % MIN_SECRET_LEN)
# A list of one-character strings, as Selenium sends a typed value: "value":["1","2","3","4"].
_CHAR_LIST = re.compile(r"""\[\s*(?:(["'])[^"'\\]\1\s*,\s*)*(["'])[^"'\\]\2\s*\]""")
_ONE_CHAR = re.compile(r"""["']([^"'\\])["']""")

# `/Users/<name>` or `/home/<name>`: a home folder, wherever it came from (the nightly clone's
# records name a different repository root than this one).
# Not inside a URL or another path: `https://host/home/x` is somebody's web page, not a home folder.
# A `file://` URL is a local path, so it counts.
_HOME = re.compile(r"(?:(?<=file://)|(?<![\w.~/-]))/(?:Users|home)/[^/\s'\"<>:;,)\]}]+")


def _mask_known(text: str) -> str:
    if not _secrets:
        return text

    def quoted(match):
        return f"{match.group(1)}{MASK}{match.group(1)}" if match.group(2) in _secrets else match.group(0)

    def char_list(match):
        joined = "".join(_ONE_CHAR.findall(match.group(0)))
        return f'["{MASK}"]' if joined in _secrets else match.group(0)

    text = _QUOTED.sub(quoted, text)
    text = _CHAR_LIST.sub(char_list, text)
    for secret in sorted(_secrets, key=len, reverse=True):
        if len(secret) >= FREE_TEXT_MIN_LEN:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])", MASK, text)
    return text


def _mask_shapes(text: str) -> str:
    text = _PIN_QUOTED.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}{m.group(2)}", text)
    text = _PIN_BARE.sub(lambda m: f"{m.group(1)}{MASK}", text)
    text = _UNLOCK_KEY.sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
    return _UNLOCK_SAID.sub(lambda m: f"{m.group(1)}{MASK}", text)


def _shorten_paths(text: str) -> str:
    root = str(REPO_ROOT)
    text = text.replace(root + os.sep, "").replace(root, ".")
    return _HOME.sub("~", text)


def redact(text) -> str:
    """`text` with every PIN masked and every local path shortened. Safe on any input."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return text
    return _shorten_paths(_mask_shapes(_mask_known(text)))


def redact_json(value):
    """A JSON-shaped value with every string in it redacted. Keys are left as they are."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json(v) for v in value]
    return value


# --- Appium's device unlock --------------------------------------------------------------------

# Masking the key is not enough for the unlock: Appium's uiautomator strategy then reads every
# keypad key's text and clicks keys by element id, so the order of the clicks spells the PIN. The
# whole exchange is left out of the record instead.
_UNLOCK_BEGINS = ("mobile: unlock", "systemui:id/digit_text", "unlock device using pin")
_UNLOCK_ENDS = re.compile(r"<-- POST \S*/execute/sync\b")


def drop_unlock_exchanges(messages: Iterable[str]) -> List[str]:
    """Appium log messages with every device-unlock exchange replaced by one line saying so.

    An exchange runs from the `mobile: unlock` request (or, when the log window starts inside one,
    the first keypad lookup) to the `<-- POST .../execute/sync` that answers it. An exchange with
    no answer in sight runs to the end: dropping too much is the safe way to be wrong here.
    """
    out: List[str] = []
    dropped = 0

    def close():
        nonlocal dropped
        if dropped:
            out.append(f"[redacted] {dropped} Appium log lines of the device unlock")
            dropped = 0

    inside = False
    for message in messages:
        text = message if isinstance(message, str) else str(message)
        if not inside and any(marker in text for marker in _UNLOCK_BEGINS):
            inside = True
        if inside:
            dropped += 1
            if _UNLOCK_ENDS.search(text):
                inside = False
                close()
            continue
        out.append(text)
    close()
    return out


# --- PIN entry on an app's own keypad ------------------------------------------------------------

# A wallet's PIN screen is tapped one key at a time, and each tap's element lookup names its digit
# ("quick_pin_digit_3", content-desc="3"), so the lookups in Appium's log spell the app PIN. The
# pin pages type inside `typing_secret()`; `capture_appium_logs` leaves out every Appium entry
# stamped inside such a window. Appium runs on this machine, so its timestamps and ours agree; the
# margin covers the clocks' granularity, and dropping a neighbouring line is the safe way to err.
_WINDOW_MARGIN_MS = 50
_secret_windows: List[tuple] = []


@contextmanager
def typing_secret():
    """Mark the Appium traffic of the enclosed block as PIN entry, to be left out of appium.log."""
    start = time.time() * 1000 - _WINDOW_MARGIN_MS
    try:
        yield
    finally:
        _secret_windows.append((start, time.time() * 1000 + _WINDOW_MARGIN_MS))
        del _secret_windows[:-200]   # a run types a few hundred PINs at most; keep the newest


def in_secret_window(timestamp_ms) -> bool:
    """True if an Appium entry at this time (ms since the epoch) belongs to a PIN entry."""
    return any(start <= timestamp_ms <= end for start, end in _secret_windows)


def clear_secret_windows() -> None:
    """Forget every recorded PIN-entry window. For tests."""
    _secret_windows.clear()


# --- logging -----------------------------------------------------------------------------------

_INSTALLED = "_redacts_pins_and_paths"


def install_log_redaction() -> None:
    """Redact every log record as it is created, so every handler only ever sees clean text.

    Covers test.log, pytest's live log (pytest.out, the nightly log), the captured log in
    report.html and third-party loggers (Selenium logs request bodies at DEBUG). Installed once
    per process: `runners/run_tests.py` runs pytest repeatedly in one interpreter.

    The message is rendered, redacted and stored with no arguments left to format, the traceback
    is rendered and stored as `exc_text` (which formatters use instead of rendering their own), and
    a message that cannot be rendered is kept as its redacted template — never with its raw
    arguments, which logging's own error report would print.

    `logging.makeLogRecord` does not go through the factory; nothing in this repository uses it.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _INSTALLED, False):
        return

    def factory(*args, **kwargs):
        record = current(*args, **kwargs)
        try:
            try:
                message, suffix = record.getMessage(), ""
            except Exception:
                message, suffix = str(record.msg), " [unformattable log message]"
            record.msg = redact(message) + suffix
            record.args = ()
            if record.exc_info and record.exc_info is not True:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            record.msg, record.args = "[log message withheld: it could not be redacted]", ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    setattr(factory, _INSTALLED, True)
    logging.setLogRecordFactory(factory)
