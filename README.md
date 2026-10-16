# Automated Digital Credential Wallet Testing

Automated digital credential wallet testing script using Python, Pytest and Appium. 

## Current status

The script is currently tested using Android Virtual Devices (AVD) with plans to add iOS and physical devices.

The test results from this implementations can be seen in compact form here: https://interop.findy.fi/

## Documentation

| Document | Contents |
|---|---|
| This file | Requirements, setup, running tests, configuration, adding a new wallet |
| [`wallets/README.md`](wallets/README.md) | Wallet directory layout and the **capability matrix** — what the suite can do with each wallet (install, onboard, issue, verify, count, review, delete, …). Check and update it whenever wallet code changes. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contribution guidelines |

## Requirements

- Python 3.9+
- Node.js with [Appium](https://appium.io) [Appium Install](https://appium.io/docs/en/2.0/quickstart/install/)
- Appium UiAutomator2 driver (`appium driver install uiautomator2`) [More info](https://appium.io/docs/en/2.0/quickstart/uiauto2-driver/)
- Android emulator running with Google Play Store support
- Android SDK (`adb` in PATH)

## Setup

**1. Create an Android emulator** with Google Play Store support and enough storage:

The current development environment uses Android Studio Virtual Device Manager to create needed testing device. **Log in to Google Play Store with testing account so that applications can be downloaded!**


The name of the device can be found using the following command:

```bash 
emulator -list-avds
```

Run the AVD

```bash
emulator -avd <AVD_NAME>
```

The fingerprint sensor emulation needs to be done by hand so that it can be used within the script. 

From Settings go to Security and to set up fingerprint. Set up pin before Android lets you set up fingerprint. **Instead of usign the emulators UI use adb through terminal**

```bash
# Use this command to set up the fingerprint
adb emu finger touch 1
```

**2. Install Python dependencies:**

```bash
python -m venv <your_env_name>
source <your_env_name>/bin/activate
pip install -r requirements.txt
```

**3. Create your `.env`** with the device this machine tests against:

```bash
cp env.example .env

# The adb serial for DEVICE_NAME comes from:
adb devices -l
```

```
# .env  (gitignored — real serials and PINs never enter the repository)
DEVICE_NAME=emulator-5554
DEVICE_PIN=1234
```

`config/device.json` is committed with `${DEVICE_NAME}` / `${DEVICE_PIN}` placeholders that resolve
from `.env` at runtime, so it needs no editing. A wallet that runs on a *different* device than the
rest overrides them with its own variables — heidi uses `${HEIDI_DEVICE_NAME}` and
`${HEIDI_DEVICE_PIN}` because it targets the emulator while the others target a phone. See
`env.example` for the full list.

**4. Start Appium:**

```bash
# Just appium
appium

# Appium with inspector for development purposes
appium --allow-cors --use-plugins=inspector

# Appium with adb_shell enabled (required for wallets that need direct am-start targeting)
appium --allow-insecure adb_shell
```

> **Note:** The `--allow-insecure adb_shell` flag is needed for wallet implementations that bypass Android's intent chooser by targeting a specific app component directly. Without it, `mobile: shell` commands will fail with a security error.


## Running tests

With Appium running and the emulator started, run tests with:

```bash
# Run all tests for one wallet
pytest wallets/<wallet>/tests/

# Run the specific test
pytest wallets/<wallet>/tests/<test_python_file>

# Run with verbose output
pytest wallets/<wallet>/tests/ -v

# Run all wallets in sequence
python runners/run_tests.py

# Run specific wallet(s) in sequence
python runners/run_tests.py <wallet1_name> <wallet2_name> <wallet3_name>

# For example running the example wallet would be
python runners/run_tests.py example
```


## Project structure

```
├── base/
│   ├── base_page.py            # Core interactions
│   ├── base_test.py            # Test setup and Play Store install
│   ├── config.py               # Loads the JSON configs and resolves ${VAR} against .env
│   ├── test_cases.py           # Builds the issuance/verification case list every wallet shares
│   ├── play_store_analyzer.py  # Play Store state detection
│   └── android.py              # Android system overlay detection (biometric prompt, etc.)
├── providers/
│   ├── base.py                 # DeeplinkProvider interface
│   ├── web_provider.py         # Fetches deeplinks from issuer web pages
│   ├── api_provider.py         # Calls a provider's JSON API; the request is config
│   ├── multipaz_provider.py    # Walks the Multipaz sandbox issuer's pre-authorized offer form
│   ├── config_provider.py      # Returns static deeplinks from config
│   ├── itb_provider.py         # Drives ITB test sessions and retrieves deeplinks via WebSocket
│   └── factory.py              # Selects the right provider based on config
├── wallets/
│   ├── README.md               # Wallet layout + capability matrix (keep it up to date)
│   ├── example/                # Template wallet — copy this to start a new wallet
│   └── <wallet>/
│       ├── config.json         # Wallet-specific config (package name, PIN, timeouts)
│       ├── pages/              # Page Object Model classes
│       ├── flows/              # Multi-step user flows
│       └── tests/              # Test files
├── config/
│   ├── device.json             # Shared infrastructure config (${VAR} placeholders, filled from .env)
│   └── providers.json          # The issuers and verifiers every wallet is tested against
├── env.example                 # Every environment variable the configs expect — copy to .env
├── .env                        # Your real device serials, PINs and credentials (gitignored)
├── runners/
│   └── run_tests.py            # Runs all wallets in sequence under one report directory
├── reports/
│   └── <DATE_TIME>             # Report with the specific time stamp
│       └── <wallet>/           # Wallet specific logs and report (screenshots, app.log, test.log, etc.)
└── conftest.py                 # Shared fixtures (driver, app, reporting)
```

## Configuration

### Machine-specific values come from `.env`

**Any string in any config file may reference an environment variable as `${VAR}`**, resolved when
the config is loaded. That keeps device serials, PINs and credentials out of the committed JSON:

```json
"device_name": "${DEVICE_NAME}",
"device_pin":  "${DEVICE_PIN}"
```

- `.env` at the project root holds the real values and is gitignored; `env.example` lists every
  variable the configs expect. Copy it to `.env` and fill it in.
- Shell exports and CI/CD injection take precedence over `.env`.
- **A referenced variable that isn't set fails the run immediately**, naming the variable and the
  config key that used it — rather than passing a literal `"${DEVICE_NAME}"` through to Appium.
- Keep comments on their own lines in `.env`. Everything after the first `=` is the value, so a
  trailing `# comment` becomes part of it.

**`config/device.json`** — shared infrastructure settings (committed with placeholders; put your
values in `.env` as shown in Setup). Beyond the Appium/device keys, it carries two run-wide switches:

```json
{
    "recording": {
        "enabled": true
    },
    "updates": {
        "enabled": true,
        "timeout": 900
    }
}
```

| Key | Meaning |
|:----|:--------|
| `recording.enabled` | Save an `.mp4` screen recording per test under `reports/<ts>/<wallet>/recordings/`. |
| `updates.enabled` | Before the first test of each wallet, check the Play Store and install a pending update, recording the outcome in `app_info.json`. Set `false` to test whatever build is on the device. |
| `updates.timeout` | Wall-clock seconds to wait for an update to finish installing. If it can't be confirmed within this budget the whole wallet session fails, since the app would be mid-replacement — so keep it generous. |

Either block may be overridden per wallet in `wallets/<wallet>/config.json`; the wallet's value replaces the device-level one entirely.

**`wallets/<wallet>/config.json`** — wallet-specific settings:
```json
{
    "application": {
        "package": "com.example.wallet",
        "activity": ".MainActivity",
        "pin": "123456"
    },
    "timeouts": {
        "default": 10,
        "pin_digit_delay": 0.3,
        "credential_offer": 30
    },
    "reporting": {
        "screenshot_on_failure": true
    },
    "onboarding": {
        "reset": false
    }
}
```

Note there is no `test_cases` here: the issuers and verifiers are shared by every wallet and live in
`config/providers.json`. At runtime `load_config()` merges the device file, the wallet file and the
selected providers, so all code reads device and wallet settings from one dict.

**`config/providers.json`** — the issuers and verifiers every wallet is tested against. They
describe the interop matrix, not any one wallet, so they are defined once:

```json
{
    "issuers": {
        "<name>": {
            "base_url": "https://issuer.example.com",
            "credentials": {
                "<credential_name>": { "type": "issuance", "path": "endpoint.json" }
            }
        }
    },
    "verifiers": {
        "<name>": {
            "base_url": "https://verifier.example.com",
            "credentials": {
                "<credential_name>": { "type": "verification", "path": "/" }
            }
        }
    }
}
```

Each entry becomes a test case named `<name>_issuer` / `<name>_verifier` — the names the compact
report charts as matrix rows, so renaming one breaks continuity with previously published runs.

An entry may also carry a `"group"`, naming where the provider comes from (`"Pension demo"`,
`"FIDES Community"`), or, for `"EUDI credentials"`, the one-row-per-credential-type matrix it
belongs to (see [Credential-typed entries](#credential-typed-entries)). The compact report gives
each group its own section, with *Issuers* and *Verifiers* under it, in the order the groups first appear here, and its run history gets a column
per group. If no entry has a group, the report is split by *Issuers* and *Verifiers* alone.

A wallet that declares its own `test_cases` block opts out of the registry entirely. That is how
`wallets/example/` stays a self-contained illustration; no real wallet does it.

### Choosing which providers a run exercises

`DEFAULT_ISSUERS` and `DEFAULT_VERIFIERS` in `.env` take the bare names from the registry, and
`<WALLET>_ISSUERS` / `<WALLET>_VERIFIERS` override them for one wallet:

```
DEFAULT_ISSUERS=hovi,waltid     # every wallet runs just these two issuers
HOVI_ISSUERS=procivis           # ...except hovi, which runs only procivis
HEIDI_VERIFIERS=none            # heidi runs no verification cases at all
```

- Blank or unset runs **every** provider in the registry, which is what published runs do.
- `none` runs none of that kind.
- A name that isn't in the registry **stops the run**, naming the valid ones. A typo would
  otherwise publish a matrix with a row quietly missing and nothing to explain it.
- The list selects, it does not order — results always follow registry order, so two runs stay
  comparable.

These lists only choose *which* providers run. Everything else about a provider, above all where it
is deployed, is configured in `config/providers.json` and nowhere else — to test the suite against
your own instance, change that entry's `base_url`.

### API issuers and verifiers

Most public demo issuers and verifiers are single-page apps: the HTML that arrives over HTTP holds
no deeplink at all, only the JavaScript that will later ask a backend for one. Those backends are
plain JSON APIs, so an entry with `type: api` calls one directly and reads the offer out of the
answer. The request is entirely config — adding a provider that works this way is a registry entry,
not Python.

```json
"eudiplo_loyalty": {
    "type": "api",
    "base_url": "https://playground.eudi-wallet.org",
    "credentials": {
        "credential_issuance": {
            "type": "issuance",
            "path": "/api/issue",
            "body": { "credentialId": "loyalty-card" }
        }
    }
}
```

| Key | Meaning |
|:----|:--------|
| `path` | Required. Appended to `base_url`. |
| `method` | `POST` (default) or `GET`. |
| `body` | JSON request body. Omitted for a `GET`. A value that is exactly `"{{uuid}}"` becomes a fresh UUID on every call, and `"{{get:<url>}}"` becomes the text of a GET to `<url>` (a path is joined to `base_url`). A 4xx on that GET fails `[no_retry]`, like one on the request itself, and any other `{{…}}` value is rejected as a typo rather than sent literally. Placeholders work in `body` only. |
| `headers` | Extra request headers. |
| `response_field` | JSON field that must hold the deeplink, dotted for a nested one (`by_reference.uri`). If it doesn't, the call fails `[no_retry]` rather than falling back to another link in the response. Leave it out to let the scan find the deeplink: a bare URL, a `uri` field, or the first deeplink anywhere in the JSON. |
| `rewrite_scheme` | A scheme such as `openid4vp` or `openid-credential-offer`. Written without `://`, and it needs `response_field`. The link in that field is returned as `<scheme>://?<its query>`, the query byte for byte; the link's host, path and fragment are dropped. For Lissi, which answers with a link into its own web wallet (`https://wallet.lissi.io/lite?request_uri=…`): Android would hand that to a browser, never to the wallet under test, and its query is the request itself. |

The two placeholders exist for one provider each. Hovi's playground refuses a `sessionId` it has
seen in the last hour, so a fixed one fails every run but the first. The Findynet IPS demo posts
the 50 kB sample bundle its own page fetches from beside it, and fetching the same file keeps it
out of the registry. They are written `{{…}}` because the config loader expands `${…}` from the
environment before a provider ever sees the value.

Without `response_field` or `rewrite_scheme`, the response is read with the same scan the web
provider uses, so an API that answers with a bare URL and one that answers with JSON both work
without config saying which. A 4xx is raised as
`[no_retry]`: the provider is rejecting the configured request, and its body says why, so retrying
an unchanged request twice more only buries that message. The exceptions are 408 and 429, a
timeout or a rate limit, which stay retryable because that is exactly the transient failure a
rerun is for.

**A registry entry is not evidence.** That an API mints a well-formed offer says nothing about
whether a wallet completes the flow. In September 2026 EUDIPLO's PID offer resolved perfectly and
then died at the token endpoint, because the offer named an authorization server that only
accepted `authorization_code` while the grant was `pre-authorized_code`. Run the provider against
real wallets before adding it here, and keep only what a run actually passed. The one exception is
a row kept on purpose to show when wallets catch up with what the provider requires; its `notes`
must say so, as the Multipaz rows' do.

### Multipaz issuer

`type: multipaz` mints a pre-authorized offer from the Multipaz sandbox issuer by walking the
"Pre-authorized Offer Flow" form on its own page: start the flow for a configuration, pick a
person in the fictional system of record, and read the offer off the page it finishes on. It is a
class rather than an `api` entry because the people are listed only in a script on that page, with
ids that change on every visit, and because the person has to hold a record of the requested kind
(not everyone has a driving licence). If nobody does, it fails `[no_retry]` rather than mint an
offer with nothing to issue.

As of October 2026 these rows pass on no wallet: the token endpoint requires client
authentication or a wallet attestation, and the configurations accept only key-attestation
proofs. They are kept to show when wallets support that.

```json
"multipaz_mdl": {
    "type": "multipaz",
    "base_url": "https://issuer.multipaz.org",
    "credentials": {
        "credential_issuance": { "type": "issuance", "configuration_id": "mDL" }
    }
}
```

### Credential-typed entries

The entries in the `"EUDI credentials"` group each cover one credential type from one provider,
and carry what a report needs to describe that type without asking the provider again:

| Key | Meaning |
|:----|:--------|
| `credential_type` | `name` (the display heading, e.g. `PID as mDoc`), `format` as the metadata or request spells it (`mso_mdoc`, `dc+sd-jwt`, or the older `vc+sd-jwt`), and `doctype` or `vct`. On a verifier, the type it requests. A type outside the listed set (Lissi's own PID) says so in `notes`. |
| `issuer_metadata` | Issuers only. The URL the issuer's metadata was read from. |
| `credential_configuration_id` | Issuers only. The configuration the offer names, the first one in the metadata that matches the type unless `notes` says why another was chosen. |
| `notes` | Anything about the entry that would otherwise have to be rediscovered: why it uses the configuration it does, what it depends on, where the provider deviates from the usual. |

The signing and binding details are not copied in. `credential_signing_alg_values_supported` (or
`cryptographic_suites_supported`) and `cryptographic_binding_methods_supported` are on
`credential_configurations_supported[<credential_configuration_id>]` in the issuer metadata.
`dpop_signing_alg_values_supported` is authorization-server metadata: follow the authorization
server the offer's grant names (`authorization_server`), else `authorization_servers[0]` from the
issuer metadata, else the `credential_issuer` itself, to `/.well-known/oauth-authorization-server`,
inserted before the path as RFC 8414 says. Some issuers repeat it in their issuer metadata as well. Procivis serves its metadata only with
`Accept: application/json`, and answers 406 without it.

### ITB issuer

To test against the [FIDES Interoperability Test Bed](https://itb.ilabs.ai), add an entry with
`type: itb` to `config/providers.json`. The provider authenticates to ITB, starts a test session,
and retrieves the credential offer deeplink over WebSocket.

```json
"itb_diipv5": {
    "type": "itb",
    "base_url": "https://itb.ilabs.ai",
    "username": "${ITB_USERNAME}",
    "password": "${ITB_PASSWORD}",
    "system_id": "<system_id>",
    "credentials": {
        "credential_issuance": {
            "type": "issuance",
            "test_case_id": "<test_case_id>",
            "spec_id": "<spec_id>",
            "actor_id": "<actor_id>",
            "provide_step": "<provider_step>"
        }
    }
}
```

`system_id`, `test_case_id`, `spec_id`, `actor_id`, and `provide_step` are organisation-specific values from your ITB registration. External users need to register their own wallet system in ITB to obtain their own IDs.

**Known limitation.** `system_id` identifies the *wallet* to ITB, so one shared registry entry
cannot serve every wallet the way the demo issuers do. Until the registry can express a per-wallet
value, an ITB case needs the wallet to declare its own `test_cases` block — which opts that wallet
out of the shared registry entirely. No wallet does this today.

ITB credentials go in the same `.env` as everything else (see
[Machine-specific values come from `.env`](#machine-specific-values-come-from-env)):

```
# .env
ITB_USERNAME=your@email.com
ITB_PASSWORD=yourpassword
```

The `${ITB_USERNAME}` and `${ITB_PASSWORD}` placeholders in config are resolved at runtime, so the credentials never need to appear in config files.

## Implementation notes

### Intentional Appium bypasses

Most device interaction goes through Appium, but two parts of the framework use `adb` via `subprocess` directly because Appium has no equivalent API:

| File | Command | Reason |
|------|---------|--------|
| `conftest.py` | `adb logcat` | Streams device logs to `reports/<run>/app.log` for the whole session. Appium has no logcat streaming API. |
| `base/utils.py` | `adb shell dumpsys package` | Reads app version and build number for reporting. Appium has no package metadata API. |

Some wallet implementations may also need to use `mobile: shell` (which requires `--allow-insecure adb_shell` on the Appium server) to send intents directly to a specific app component, bypassing Android's intent chooser dialog. This is needed when multiple wallets on the same device register for the same URI scheme (e.g. `openid-credential-offer://`).

## Adding a new wallet

1. Copy `wallets/example/` to `wallets/<wallet>/`
2. Update `config.json` with the app's package name, activity, PIN, and test cases
3. Fill in the TODO locators in `pages/` by inspecting screens with `adb` or Appium Inspector
4. Implement the flows in `flows/` following the inline TODO comments
5. The wallet is automatically picked up by `run_tests.py`
6. Add a column for it in the [capability matrix](wallets/README.md) and keep it updated as flows land
