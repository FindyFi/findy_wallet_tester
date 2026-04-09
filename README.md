# Automated Digital Credential Wallet Testing

Automated digital credential wallet testing script using Python, Pytest and Appium. 

## Current status

The script is currently tested using Android Virtual Devices (AVD) with plans to add iOS and physical devices.

[x] Script tested to work
[x] First implementations done
[ ] Wallet implementations published
[ ] iOS support

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

**3. Create `config/device.json`** with your Appium server and device settings:

```bash
# NAME_OF_THE_DEVICE can be found using adb and the following command:
adb devices -l
```

```json
{
    "server": "http://127.0.0.1:4723",
    "android": {
        "platform_name": "Android",
        "device_name": "<NAME_OF_THE_DEVICE>",
        "automation_name": "UiAutomator2"
    }
}
```

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
│   ├── play_store_analyzer.py  # Play Store state detection
│   └── android.py              # Android system overlay detection (biometric prompt, etc.)
├── providers/
│   ├── base.py                 # DeeplinkProvider interface
│   ├── web_provider.py         # Fetches deeplinks from issuer web pages
│   ├── config_provider.py      # Returns static deeplinks from config
│   ├── itb_provider.py         # Drives ITB test sessions and retrieves deeplinks via WebSocket
│   └── factory.py              # Selects the right provider based on config
├── wallets/
│   ├── example/                # Template wallet — copy this to start a new wallet
│   └── <wallet>/
│       ├── config.json         # Wallet-specific config (package name, test cases, timeouts)
│       ├── pages/              # Page Object Model classes
│       ├── flows/              # Multi-step user flows
│       └── tests/              # Test files
├── config/
│   └── device.json             # Shared infrastructure config
├── runners/
│   └── run_tests.py            # Runs all wallets in sequence under one report directory
├── reports/
│   └── <DATE_TIME>             # Report with the specific time stamp
│       └── <wallet>/           # Wallet specific logs and report (screenshots, app.log, test.log, etc.)
└── conftest.py                 # Shared fixtures (driver, app, reporting)
```

## Configuration

**`config/device.json`** — shared infrastructure settings (template committed, modify manually as shown above).

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
        "skip_if_done": true
    },
    "test_cases": {
        "<issuer_name>": {
            "base_url": "https://issuer.example.com",
            "credentials": {
                "<credential_name>": { "type": "issuance", "path": "endpoint.json" }
            }
        },
        "<verifier_name>": {
            "base_url": "https://issuer.example.com",
            "credentials": {
                "<credential_name>": { "type": "verification", "path": "/" }
            }
        }
    }
}
```

At runtime `load_config()` merges both files so all code can access device and wallet settings from one dict.

### ITB issuer

To test against the [FIDES Interoperability Test Bed](https://itb.ilabs.ai), use `type: itb` in the issuer config. The provider authenticates to ITB, starts a test session, and retrieves the credential offer deeplink over WebSocket.

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

`system_id`, `test_case_id`, `spec_id`, `actor_id`, and `provide_step` are organisation-specific values from your ITB registration. The wallet directories in this repo already have the correct values set for the FindyNet organisation. External users need to register their own wallet system in ITB to obtain their own IDs.

ITB credentials are loaded from a `.env` file at the project root (gitignored):

```
# .env
ITB_USERNAME=your@email.com
ITB_PASSWORD=yourpassword
```

The `${ITB_USERNAME}` and `${ITB_PASSWORD}` placeholders in config are resolved at runtime, so the credentials never need to appear in config files. Shell environment variables and CI/CD injection take precedence over `.env`.

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
