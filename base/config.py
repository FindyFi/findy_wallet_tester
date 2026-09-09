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


def _override_name(name: str, wallet: str) -> Optional[str]:
    """The wallet-specific variable that outranks `name`, or None when there is none.

    `DEFAULT_RESET` + hovi -> `HOVI_RESET`: the shared prefix is replaced, not stacked.
    `DID_METHODS` + gataca -> `GATACA_DID_METHODS`: a bare name is simply prefixed, so the
    convention reaches settings that have no fleet-wide spelling.
    `GATACA_APP_PIN` + gataca -> None: a name already carrying the wallet's prefix is not
    prefixed twice.
    """
    if not wallet or name.upper().startswith(f"{wallet.upper()}_"):
        return None
    stem = name[len(_SHARED_PREFIX):] if name.startswith(_SHARED_PREFIX) else name
    return f"{wallet.upper()}_{stem}"


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
    override = _override_name(name, wallet)
    if override and os.environ.get(override):
        return os.environ[override], override
    return os.environ.get(name), name


def _lookup(name: str, wallet: str = "") -> Optional[str]:
    """The value `_resolve` finds, for callers that do not need to report which variable it was."""
    return _resolve(name, wallet)[0]


def _resolve_required(name: str, wallet: str = "") -> Tuple[Optional[str], str]:
    """Return (value, variable name) for a required ``${VAR}``, wallet override first.

    The same `<WALLET>_<KEY>` beats `<KEY>` rule as `_resolve`, so the convention holds for every
    setting rather than only the ones written in the `${DEFAULT_X:-y}` form. Without this,
    `TOPPAN_DEVICE_NAME` in `.env` was **silently ignored** and the run went to the phone anyway —
    a setting that looks configured and is not, which is the failure this config layer exists to
    remove.

    A name that already carries the wallet's own prefix is not prefixed twice: heidi's
    `${HEIDI_DEVICE_NAME}` looks up `HEIDI_DEVICE_NAME`, never `HEIDI_HEIDI_DEVICE_NAME`.

    Unset stays an error rather than becoming an empty string, which is the whole point of the
    required form. Blank counts as unset here, matching `_resolve` and the `.env` header — so a
    device with no lock screen is expressed by leaving the base `DEVICE_PIN` blank, which
    substitutes the empty string that `conftest.py` reads as "no lock".
    """
    override = _override_name(name, wallet)
    if override and os.environ.get(override):
        return os.environ[override], override
    return os.environ.get(name) or None, name


def _expand_env(value, missing: list, where: str = "", wallet: str = ""):
    """Substitute ${VAR} and ${VAR:-default} from the environment through a config structure.

    Any value in any config file can reference an environment variable, so machine-specific
    settings live in the gitignored `.env` instead of in committed JSON.

    Two forms, and the difference matters:

    - ``${VAR}`` is **required**. Unset variables are collected in `missing` and reported together,
      rather than left as a literal "${VAR}" — that used to surface as a device named
      "${DEVICE_NAME}" and an Appium error three steps later. A per-wallet override applies here
      too (`TOPPAN_DEVICE_NAME` beats `DEVICE_NAME`), so the naming convention means the same thing
      everywhere. heidi is unaffected: its config names `${HEIDI_DEVICE_NAME}` outright, so
      forgetting that variable still stops the run rather than quietly sending heidi to the phone.
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
        unresolved = []

        def substitute(match):
            name = match.group(1)
            resolved, var = _resolve_required(name, wallet)
            if resolved is None:
                unresolved.append(name)
                hint = "" if var != name or not wallet else f" or {wallet.upper()}_{name}"
                missing.append(f"{name}{hint} (used by {where})")
                return match.group(0)
            return resolved

        result = _ENV_PLACEHOLDER.sub(substitute, value)

        # A "${" still standing, with nothing reported unset, means the text is neither a bare
        # ${VAR} nor a whole-value ${VAR:-default}. Almost always a nested default — which looks
        # like it should work and does not, and used to pass straight through to Appium as a
        # literal device name. Say so here rather than three steps later.
        if "${" in result and not unresolved:
            raise pytest.UsageError(
                f"{where}: {value!r} is not a placeholder this loader understands.\n"
                "  Two forms are supported, and neither nests inside the other:\n"
                "    ${VAR}              required — the run stops if it is unset\n"
                "    ${VAR:-literal}     optional — 'literal' is used when VAR is unset\n"
                f"  A per-wallet override needs no syntax at all: ${{VAR}} already prefers "
                f"{(wallet or '<WALLET>').upper()}_VAR when that is set."
            )
        return result
    return value


_REGISTRY_PATH = _ROOT / "config" / "providers.json"

# Registry section -> the suffix its generated test-case key carries. The suffix is not cosmetic:
# the compact report reads each matrix row's identity straight out of the parametrize id, and
# status/icons/agents/ is keyed the same way ("hovi_issuer.png"), so a renamed key silently breaks
# continuity with every run published so far.
_ROLES = (("issuers", "issuer"), ("verifiers", "verifier"))


def as_list(value) -> list:
    """A config value that names several things, as a list of bare names.

    Accepts what an operator plausibly writes, because these arrive from two directions: a JSON
    list committed in a config file, or a single string once `.env` has supplied it.

        ["jwk", "ebsi"]     already a list
        jwk,ebsi            comma-separated, the usual .env spelling
        jwk ebsi            whitespace-separated
        ["jwk", "ebsi"]     the JSON spelling, written into .env as text
    """
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    names = [n.strip().strip("\"'") for n in re.split(r"[,\s]+", str(value).strip().strip("[]"))]
    return [n for n in names if n]


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

    names = as_list(raw)
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
def wallet_config(wallet_name: str) -> dict:
    """The wallet's own config.json, environment-expanded, without config/device.json merged in.

    For settings a test module needs at **collection** time, before any fixture exists. It cannot
    use `load_config` for that: config/device.json carries the required `${DEVICE_NAME}` and
    `${DEVICE_PIN}`, and collection is far wider than execution — `pytest wallets/ -k gataca` still
    collects heidi — so requiring every wallet's device variables to be set just to list the tests
    would be wrong.

    The alternative, reading config.json raw, is worse: it bypasses expansion entirely and hands
    back the literal "${...}" string. That is what made all three test_cleanup.py files raise
    TypeError during the first .env migration.

    Cached, and the result is shared: treat it as read-only.
    """
    raw = json.loads((_ROOT / "wallets" / wallet_name / "config.json").read_text())
    missing: list = []
    expanded = _expand_env(raw, missing, wallet=wallet_name)
    if missing:
        raise pytest.UsageError(
            f"Unset environment variable(s) referenced by the {wallet_name} config:\n  "
            + "\n  ".join(sorted(set(missing)))
            + "\nSet them in the project's .env file (see env.example) or export them."
        )
    return expanded


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


def _deep_merge(base: dict, override: dict) -> dict:
    """Overlay `override` on `base` key by key, recursing into nested dicts.

    A shallow `{**device, **wallet}` made a wallet restate an entire section to change one key of
    it. heidi overrides `android.device_name`, so it also had to copy `platform_name`,
    `automation_name` and `server` — and silently dropped `expected_locale`, which it never meant
    to touch. Worse, the drop is invisible: a key added to config/device.json simply never reaches
    the wallets that happen to declare the same section.

    A wallet should say only what differs from the fleet.
    """
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(wallet_name):
    device = json.loads((_ROOT / "config" / "device.json").read_text())
    wallet = json.loads((_ROOT / "wallets" / wallet_name / "config.json").read_text())
    merged = _deep_merge(device, wallet)

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
