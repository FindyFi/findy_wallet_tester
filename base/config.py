"""Configuration loading: JSON committed in the repo, machine-specific values from the environment.

Lives here rather than in the root `conftest.py` because the wallet test modules build their
parametrize lists at **collection** time, before any fixture exists, and need the same expansion
the fixtures get. A test module cannot import the root conftest, so the machinery has to sit
somewhere both can reach.

Three layers, in descending precedence:

    <WALLET>_<KEY>      this wallet only
    DEFAULT_<KEY>       every wallet
    the JSON literal    committed in config/*.json or wallets/<wallet>/config.json
"""
import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Load KEY=VALUE pairs from .env at the project root into os.environ.

    Values already set in the environment are not overwritten, so shell exports
    and CI/CD environment injection always take precedence over the .env file.
    """
    env_path = _ROOT / ".env"
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


# At import, so that anything importing this module sees a populated environment. The root
# conftest imports it before everything else for exactly that reason.
_load_dotenv()


_ENV_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")

# ${VAR:-default}: the whole value is one placeholder carrying its own fallback.
_ENV_DEFAULTED = re.compile(r"^\$\{(\w+):-([^}]*)\}$")

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}


def _coerce(text: str):
    """Turn an environment string into the JSON type it is standing in for.

    Environment variables are always strings, but the settings they now replace are booleans and
    numbers. Without this, `SKIP_IF_DONE=false` arrives as the string "false", which is **truthy**,
    so a wipe would be permanently on while looking configured.

    Numbers are parsed before the boolean words on purpose. "0" and "1" are both plausible numbers
    and plausible booleans, and reading them as booleans breaks the numeric settings:
    `${MAX_CREDENTIALS:-0}` would yield False, which then prints as "False" in every log line about
    the cleanup target. Left as ints they still behave correctly where a boolean is wanted, since
    Python already treats 0 as false and 1 as true.
    """
    stripped = text.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    lowered = stripped.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return text


_SHARED_PREFIX = "DEFAULT_"


def _resolve(name: str, wallet: str = "") -> Tuple[Optional[str], str]:
    """Return (value, variable name) for one placeholder, wallet override first.

    A setting written in the JSON as ``${DEFAULT_RESET:-true}`` can be set three ways, in
    descending precedence:

        HOVI_RESET=false     per-wallet override; the wallet prefix marks it as wallet-specific
        DEFAULT_RESET=false  the shared default, applying to every wallet
        (neither set)        the literal default committed in the JSON

    Naming is the whole point of the convention: anything beginning `DEFAULT_` is fleet-wide,
    anything beginning with a wallet name applies to that wallet alone, and you can tell which is
    which from `.env` without reading any code.

    The variable name comes back too so error messages can name the one actually consulted.
    """
    if wallet and name.startswith(_SHARED_PREFIX):
        override = f"{wallet.upper()}_{name[len(_SHARED_PREFIX):]}"
        if os.environ.get(override):
            return os.environ[override], override
    return os.environ.get(name), name


def _lookup(name: str, wallet: str = "") -> Optional[str]:
    """The value `_resolve` finds, for callers that do not need to report which variable it was."""
    return _resolve(name, wallet)[0]


def _expand_env(value, missing: list, where: str = "", wallet: str = ""):
    """Substitute ${VAR} and ${VAR:-default} from the environment through a config structure.

    Any value in any config file can reference an environment variable, so machine-specific
    settings live in the gitignored `.env` instead of in committed JSON.

    Two forms, and the difference matters:

    - ``${VAR}`` is **required**. Unset variables are collected in `missing` and reported together,
      rather than left as a literal "${VAR}" — that used to surface as a device named
      "${DEVICE_NAME}" and an Appium error three steps later. No wallet override is applied here:
      heidi already writes `${HEIDI_DEVICE_NAME}` explicitly, and silently falling back to the
      shared `DEVICE_NAME` would run heidi on the phone instead of its emulator.
    - ``${DEFAULT_X:-default}`` is **optional** and takes a per-wallet override (see `_resolve`).
      Unset means use the literal committed in the JSON, so the file stays meaningful and a machine
      opts in to a setting rather than every machine having to declare one. The result is
      type-coerced, so booleans and numbers survive the trip through the environment.
    """
    if isinstance(value, dict):
        return {k: _expand_env(v, missing, f"{where}.{k}" if where else k, wallet)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, missing, f"{where}[{i}]", wallet) for i, v in enumerate(value)]
    if isinstance(value, str):
        defaulted = _ENV_DEFAULTED.match(value.strip())
        if defaulted:
            name, fallback = defaulted.group(1), defaulted.group(2)
            # A quoted default declares "this setting is text, never coerce it". PINs are the
            # reason: "123456" must stay a string, because the pin pages type it digit by digit
            # and an int cannot be iterated (and "0123" would lose its leading zero).
            is_text = len(fallback) >= 2 and fallback[0] == fallback[-1] and fallback[0] in "\"'"
            override = _lookup(name, wallet)
            if override:
                return override if is_text else _coerce(override)
            return fallback[1:-1] if is_text else _coerce(fallback)
        expanded = os.path.expandvars(value)
        for name in _ENV_PLACEHOLDER.findall(expanded):
            missing.append(f"{name} (used by {where})")
        return expanded
    return value


_REGISTRY_PATH = _ROOT / "config" / "providers.json"

# Registry section -> the suffix its generated test-case key carries. The suffix is not cosmetic:
# the compact report reads each matrix row's identity straight out of the parametrize id, and
# status/icons/agents/ is keyed the same way ("hovi_issuer.png"), so a renamed key silently breaks
# continuity with every run published so far.
_ROLES = (("issuers", "issuer"), ("verifiers", "verifier"))


def _selected_providers(section: str, available: list, wallet: str) -> list:
    """Return the providers of one section this wallet runs, in registry order.

    Chosen with the same convention as every other setting, so `DEFAULT_ISSUERS` applies to the
    whole fleet and `HOVI_ISSUERS` overrides it for hovi alone:

        (unset or blank)        every provider in the registry
        procivis,hovi           those two
        ["procivis", "hovi"]    the same, for anyone who writes lists that way
        none                    no providers of this kind

    Blank has to mean "all" rather than "none" because `env.example` ships every key present and
    empty, and because it keeps this whole mechanism a no-op until someone opts in.

    An unrecognised name is an error rather than a silent omission. Results are published, so a
    typo that quietly dropped a provider would ship a matrix with a row missing and nothing
    anywhere to say why.

    The list selects; it does not order. Results follow the registry so two runs stay comparable.
    """
    raw, var = _resolve(f"{_SHARED_PREFIX}{section.upper()}", wallet)
    if raw is None or not raw.strip():
        return list(available)

    names = [n.strip().strip("\"'") for n in re.split(r"[,\s]+", raw.strip().strip("[]"))]
    names = [n for n in names if n]
    if [n.lower() for n in names] == ["none"]:
        return []

    unknown = [n for n in names if n not in available]
    if unknown:
        raise pytest.UsageError(
            f"{var} names {'a provider' if len(unknown) == 1 else 'providers'} that "
            f"config/providers.json does not define: {', '.join(sorted(unknown))}.\n"
            f"  Known {section}: {', '.join(available)}\n"
            "  Use the bare name as it appears there (no _issuer/_verifier suffix), or 'none' to "
            "run none of them."
        )
    return [n for n in available if n in names]


@lru_cache(maxsize=None)
def provider_matrix(wallet_name: str) -> dict:
    """Return the wallet's `test_cases`: the shared registry, filtered by the .env selection.

    Issuers and verifiers are defined once in `config/providers.json` and shared by every wallet —
    they describe the interop matrix, not the wallet. The keys generated here (`hovi_issuer`,
    `waltid_verifier`, ...) are what the report charts, so they match what the per-wallet configs
    used to spell out by hand.

    A wallet that declares its own `test_cases` opts out of the registry entirely. That is how
    `wallets/example/` keeps the self-contained illustration a new wallet is copied from.

    Reads only the registry and the wallet's own config, never `config/device.json`, so collecting
    a wallet's tests does not require that wallet's device variables to be set — collection is
    much wider than execution, and `pytest wallets/ -k hovi` still collects heidi.

    Cached, and the result is shared: treat it as read-only, or take a copy (`load_config` does).
    """
    wallet_cfg = json.loads((_ROOT / "wallets" / wallet_name / "config.json").read_text())
    missing: list = []

    if "test_cases" in wallet_cfg:
        cases = _expand_env(wallet_cfg["test_cases"], missing, "test_cases", wallet_name)
    else:
        registry = json.loads(_REGISTRY_PATH.read_text())
        cases = {}
        for section, suffix in _ROLES:
            entries = registry.get(section, {})
            for name in _selected_providers(section, list(entries), wallet_name):
                cases[f"{name}_{suffix}"] = _expand_env(
                    entries[name], missing, f"{section}.{name}", wallet_name
                )

    if missing:
        raise pytest.UsageError(
            f"Unset environment variable(s) referenced by the providers {wallet_name} runs:\n  "
            + "\n  ".join(sorted(set(missing)))
            + "\nSet them in the project's .env file (see env.example) or export them."
        )
    return cases


def load_config(wallet_name):
    device = json.loads((_ROOT / "config" / "device.json").read_text())
    wallet = json.loads((_ROOT / "wallets" / wallet_name / "config.json").read_text())
    merged = {**device, **wallet}

    onboarding = merged.get("onboarding", {})
    if "skip_if_done" in onboarding:
        raise pytest.UsageError(
            f"wallets/{wallet_name}/config.json: 'onboarding.skip_if_done' has been replaced by "
            "'onboarding.reset', which reads the way an operator thinks about it: reset=true means "
            "wipe the wallet and onboard again. Rename the key and invert the value "
            "(skip_if_done: true becomes reset: false)."
        )

    # Expanded by `provider_matrix` below, which also applies the .env provider selection.
    merged.pop("test_cases", None)

    missing = []
    merged = _expand_env(merged, missing, wallet=wallet_name)
    if missing:
        raise pytest.UsageError(
            f"Unset environment variable(s) referenced by the {wallet_name} config:\n  "
            + "\n  ".join(sorted(set(missing)))
            + "\nSet them in the project's .env file (see env.example) or export them."
        )

    # A copy, because `provider_matrix` caches and callers treat their config as their own.
    merged["test_cases"] = deepcopy(provider_matrix(wallet_name))

    # `reset` is the operator-facing spelling: true means "wipe and onboard again". The flows still
    # take `skip_if_done`, which is the same switch seen from the other side, so it is derived here
    # once rather than inverted at nine call sites. When the test bodies move into base/, the
    # internal name can follow and this line goes away.
    merged.setdefault("onboarding", {})["skip_if_done"] = not merged["onboarding"].get("reset", False)
    return merged
