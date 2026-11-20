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


### Run wallets in parallel

`runners/run_parallel.py` runs the wallets at the same time, one worker per device, and puts the
next wallet on a device as soon as that device is free. Each wallet is still one `pytest` run, so
the reports look the same as with `run_tests.py`: everything lands in
`reports/<timestamp>/<wallet>/`, plus a `pytest.out` file there with the wallet's console output.
The runner's own console shows one line when a wallet starts and one when it ends, and a table of
wallet, device, state, exit code and duration at the end.

```bash
# Every wallet except example
python runners/run_parallel.py

# Only these wallets
python runners/run_parallel.py hovi paradym procivis

# Show the plan without touching a device, a server or a file
python runners/run_parallel.py --dry-run

# Extra pytest arguments: anything starting with "-" is passed on; put arguments that take a
# value after "--"
python runners/run_parallel.py hovi -x -- -k issuance
```

**Which device a wallet uses.** This comes from `.env`, in two ways:

- A wallet with a non-blank `<WALLET>_DEVICE_NAME` is *pinned*. It runs only on that device. It is
  skipped only if its device or its Appium server does not answer within `--wait` seconds (default
  30; `0` checks once). A device counts as ready when `adb get-state` says "device",
  `sys.boot_completed` is 1 and the server answers. A device is busy when another `adb logcat`
  capture is streaming from it, which is what every test run, this runner's or `run_tests.py`'s,
  keeps up while a wallet runs. A busy device is waited for up to `--busy-wait` seconds (default
  2400, 40 minutes, longer than the longest wallet so far), so you can start this runner while
  another run is still going and its wallets queue behind it. Ctrl-C stops the wait.
  A wallet whose configuration cannot be read, or that cannot be started, is skipped as well.
- Every other wallet is *floating*. Floating wallets wait in one shared queue and any ready device
  takes the next one. A device that does not answer takes no work, and floating wallets are never
  skipped because one device is down. They are skipped only when no device has been alive for the
  whole `--wait`, or when no device qualifies for floating wallets (see below). A busy device and a
  device that is running a wallet both count as alive.

The device pool is every device named in the configuration: the devices of the pinned wallets, and
`DEVICE_NAME`. A device takes floating wallets only if its screen-lock PIN is known **and** its
pinned wallets agree on one Appium server. The default device uses `DEVICE_PIN`; any other device
needs every pinned wallet on it to have its own non-blank `<WALLET>_DEVICE_PIN`, all with the same
value. A device's server is the one its pinned wallets use, from their `<WALLET>_APPIUM_SERVER`
(else `DEFAULT_APPIUM_SERVER`); a device without pinned wallets uses `DEFAULT_APPIUM_SERVER`. If its
pinned wallets name different servers the device is marked ambiguous: each pinned wallet still runs
against its own server, but the device takes no floating wallets. A device that does not qualify
still runs its pinned wallets. `--dry-run` shows the pool, which devices are "PIN known", the
pinned wallets for each device and the floating queue. It never prints a PIN.

When a floating wallet runs on a device, the runner gives that one child process
`<WALLET>_DEVICE_NAME`, `<WALLET>_APPIUM_SERVER` and `<WALLET>_DEVICE_PIN` for that device. Nothing
else sees them and the runner's own environment is left alone. A floating wallet may therefore
install and onboard on a device it has not used before, which the clean-slate policy allows.

**Pin these wallets in `.env`**, because they work on only one device:

- toppan runs on the phone only (on the emulator it reports "Threat detected").
- gataca and heidi need manual onboarding on their emulator.
- authbound needs manual registration and a fingerprint (`requires_fingerprint`).
- Pinning unime is recommended too.

**Appium servers.** Two devices that use the same Appium server take their turns instead of running
together, because the server's log is shared and each wallet's `appium.log` would otherwise absorb
the other wallet's lines. To run them together, give each device its own server (`appium -p 4724`)
and set `<WALLET>_APPIUM_SERVER=http://localhost:4724` on the wallets pinned to that device, or
pass `--allow-shared-server`.

**Exit code.** 130 after Ctrl-C. Otherwise the highest pytest exit code other than 0, 1 and 5
(pytest's "all passed", "some failed" and "nothing collected"); otherwise 10 if a wallet was
skipped or never started; otherwise the highest code. A wallet the runner had to kill shows as
`killed` with exit 137. A skipped wallet gets a `pytest.out` that says why, so the
report shows it as a wallet with no data.

**Ctrl-C.** The first press sends one interrupt to each running wallet, so it can finish its
session and write its report files, and no further wallets start. A wallet that has not stopped
after 150 seconds is killed. A second press kills them straight away.

**Limits.** The runner does not stop another runner, or `run_tests.py`, from using a device. It
looks for an `adb logcat` capture on the device and waits if it finds one, which is a best-effort
check. A Python traceback that goes through the unlock code can print the PIN into `pytest.out`,
just as it already does in `report.html`. Whether two wallets on two devices can start their
UiAutomator2 sessions at the same moment without a port clash has not been verified. Compare the
first parallel run with a sequential one, since a loaded machine can make a test slower.


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
│   ├── run_tests.py            # Runs all wallets in sequence under one report directory
│   └── run_parallel.py         # Runs the wallets in parallel, one worker per device
├── reports/
│   └── <DATE_TIME>             # Report with the specific time stamp
│       └── <wallet>/           # Per-wallet artifacts: app.log (failure digest), logcat.log,
│                               #   appium.log, test.log, crashes.log (DropBox crashes/ANRs),
│                               #   summary.json, report.html, screenshots/, xml_dumps/, recordings/
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
| `recording.reencode` | Optional, default `true`. When the wallet's pytest session ends, shrink its recordings in place to 806 px height (x264, every frame kept, niced, 2 threads). Set `false` to keep them at full size. |
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
| `conftest.py` | `adb logcat` | Streams the device log to `reports/<run>/<wallet>/logcat.log` for the whole session, filtered to the wallet's own uid plus the system's (1000). Appium has no logcat streaming API. |
| `conftest.py` | `adb shell pm list packages -U` | Resolves the app's uid, so the logcat capture can be scoped to it. |
| `base/utils.py` | `adb shell dumpsys package` | Reads app version and build number for reporting. Appium has no package metadata API. |
| `conftest.py` | `adb shell dumpsys dropbox` | Reads Android's own crash/ANR store into `crashes.log` after the session. It is written by the system, so it survives a crash that kills the Appium session — unlike every live capture. |
| `conftest.py` | `adb shell date` | The device's own clock, used as the time floor for the DropBox read (device timestamps are device-local). |

Some wallet implementations may also need to use `mobile: shell` (which requires `--allow-insecure adb_shell` on the Appium server) to send intents directly to a specific app component, bypassing Android's intent chooser dialog. This is needed when multiple wallets on the same device register for the same URI scheme (e.g. `openid-credential-offer://`).

## Adding a new wallet

1. Copy `wallets/example/` to `wallets/<wallet>/`
2. Update `config.json` with the app's package name, activity, PIN, and test cases
3. Fill in the TODO locators in `pages/` by inspecting screens with `adb` or Appium Inspector
4. Implement the flows in `flows/` following the inline TODO comments
5. The wallet is automatically picked up by `run_tests.py`
6. Add a column for it in the [capability matrix](wallets/README.md) and keep it updated as flows land
