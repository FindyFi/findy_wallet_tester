# Wallets

One directory per wallet under test, plus `example/` as the template to copy when adding a new one.
Each wallet directory has the same shape:

```
<wallet>/
├── config.json     # package, activity, PIN, timeouts, test_cases (issuers + verifiers)
├── conftest.py     # per-wallet fixtures (home-screen setup, teardown, wallet-specific hooks)
├── pages/          # Page Object Model classes — locators live here
├── flows/          # multi-step user flows (init / credential / verification / …)
└── tests/          # test files, collected by pytest and by runners/run_tests.py
```

**Related docs**
- [Root `README.md`](../README.md) — setup, running tests, configuration, adding a new wallet
- This file — *what* the suite can do with each wallet (the capability matrix below)

---

## ⚠️ Keeping the capability matrix current

**The matrix below goes stale fast — check it whenever wallet code changes, and update it in the same
commit.** It is the tracking artifact for the unification work, and a wrong ✅ is worse than no matrix
at all: it hides a gap nobody is working on.

Update it when any of these happen:

- a capability is added, removed, or changes state for any wallet (e.g. `count_credentials()`
  implemented, a placeholder locator replaced with a real one, an assertion strengthened)
- a wallet app updates and breaks or changes a flow — re-check the ⚠️ rows for that wallet
- a new wallet directory is added (add a column; a fresh copy of `example/` is ❌ almost everywhere)
- a shared capability moves into or out of `base/` (🔁 rows)
- after a full test run that contradicts the matrix — the run wins, fix the matrix

Also re-read the whole matrix at the start of any unification work: it is the checklist of what has to
become uniform.

---

## Capability matrix

What the test suite can actually **do** with each wallet today. Every ❌/⚠️ is either a gap to close or
a difference to justify. Status as of 2026-08-03, branch `update/unification`.

**Legend**

| Mark | Meaning                                                                              |
|:-----|:-------------------------------------------------------------------------------------|
| ✅   | implemented and exercised by a test                                                  |
| ⚠️   | partial — code exists but is a placeholder, unasserted, or can't complete            |
| ❌   | not implemented                                                                      |
| –    | not applicable for this wallet                                                       |
| 🔁   | provided by shared code in `base/` or the root `conftest.py`, same for every wallet  |

| Capability                            | authbound | gataca | heidi | hovi | paradym | procivis | toppan | unime |
|:--------------------------------------|:---------:|:------:|:-----:|:----:|:-------:|:--------:|:------:|:-----:|
| **Lifecycle**                         |           |        |       |      |         |          |        |       |
| Install app (Play Store)              |    🔁     |   🔁   |  🔁   |  🔁  |   🔁    |    🔁    |   🔁   |  🔁   |
| Update app to a newer release         |    🔁     |   🔁   |  🔁   |  🔁  |   🔁    |    🔁    |   🔁   |  🔁   |
| Record app version                    |    🔁     |   🔁   |  🔁   |  🔁  |   🔁    |    🔁    |   🔁   |  🔁   |
| Launch app                            |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Onboard from fresh install            |    ❌     |   ❌   |  ✅   |  ✅  |   ✅    |    ✅    |   –    |  ✅   |
| Open / unlock returning wallet        |    ✅     |   ✅   |  ✅   |  –   |   ✅    |    ✅    |   –    |  ✅   |
| Reset wallet (wipe + re-onboard)      |    ⚠️      |   ⚠️    |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| **Credentials**                       |           |        |       |      |         |          |        |       |
| Issue credential (deeplink -> accept) |    ⚠️      |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Verify credential (deeplink -> share) |    ⚠️      |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Count credentials                     |    ⚠️      |   ✅   |  ✅   |  ✅  |   ✅    |    ❌    |   ✅   |  ✅   |
| **Assert** the count changed          |    ⚠️      |   ✅   |  ⚠️    |  ⚠️   |   ⚠️     |    ❌    |   ✅   |  ✅   |
| Open / review a credential's detail   |    ❌     |   ✅   |  ⚠️    |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
| Delete a single credential            |    ❌     |   ✅   |  ❌   |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
| Wipe all credentials (post-suite)     |    ❌     |   ✅   |  ❌   |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
| **Support**                           |           |        |       |      |         |          |        |       |
| Detect app error screens              |    ✅     |   ✅   |  ✅   |  ❌  |   ✅    |    ✅    |   ✅   |  ❌   |
| Manipulate wallet settings            |    ❌     |   ❌   |  ✅   |  ❌  |   ✅    |    ❌    |   ❌   |  ❌   |
| Collect in-app debug logs             |    ❌     |   ❌   |  ❌   |  ❌  |   ✅    |    ❌    |   ❌   |  ❌   |
| Wallet-specific pre-test setup        |     –     |   ✅   |   –   |  –   |    –    |    –     |   –    |   –   |

---

## Notes per capability

**Install app** — `BaseTest.setup()` (`base/base_test.py`): if `is_app_installed()` is false, deeplink
to the Play Store page and drive the install, reacting to `READY_TO_INSTALL / DOWNLOADING /
INSTALLING / POPUP / ERROR` states (bilingual EN/FI keywords, tolerates UiAutomator2 dying mid-install).
Identical for all wallets — no per-wallet code. No APK sideloading path.

**Update app** — `BaseTest.check_for_updates()`, run once per wallet session from the `app` fixture and
controlled by `updates` in `config/device.json`. It opens the same Play Store page as the install path,
and if the primary button reads **Update** it taps it and waits for the app's `versionCode` to change,
recording `update_available` / `updated` / `version_before` / `version_after` into `app_info.json`.
Identical for all wallets — no per-wallet code. Two behaviours worth knowing:

- An update that starts but can't be confirmed installed raises `UpdateNotFinished` and **fails the
  whole wallet session**, because every later result would come from an app mid-replacement. Problems
  *before* the button is tapped (no Play Store listing, unreadable `versionCode`) only warn.
- Verified end to end on 2026-08-03 across all 8 wallets for the *detect* path (each reported "up to
  date" against its real build). The *apply* path has not run against a genuine pending update yet —
  turn off Play Store auto-update on the device so updates land when the suite decides, not before.

Before this existed, a wallet update was discovered only when locators broke — as on 2026-07-30, when
unime v0.13.8 changed its password field and `pin_page.py` had to be patched by hand.

**Record app version** — `get_app_info()` parses `adb shell dumpsys package` for
`versionName`/`versionCode` and writes `reports/<ts>/<wallet>/app_info.json`. Never compared against an
*expected* version, so there is no pinning; the only comparison is the update check above, which
reports the build as of the moment the run started.

**Launch app** — `tests/test_install.py::test_app_launch`. Note the filename is misleading: it asserts
the app *launched*, not that an install happened (install is a side effect of the `app` fixture). The
file is near-identical boilerplate in every wallet.

**Onboard from fresh install** — heidi, hovi, paradym, procivis and unime drive the full first-run
sequence. authbound and gataca deliberately raise: both need server-side registration (authbound:
email/registration; gataca: email) that isn't automated, so tests assume an already-registered
wallet. toppan has no onboarding at all.

**Open / unlock returning wallet** — six different mechanisms: app passcode (authbound), device PIN via
the system biometric prompt (gataca), biometric only (heidi), app PIN (paradym, procivis), password
(unime). hovi and toppan have no lock to open.

**Reset wallet** — `init_flow.run(skip_if_done=False)` clears app data and re-onboards; triggered once
per session by `conftest_helpers.navigate_to_home` when `onboarding.skip_if_done` is false. authbound
and gataca are ⚠️: they clear the data and then raise, because they can't re-onboard — so using
`skip_if_done=false` on those two leaves the wallet unusable until someone registers it by hand.

**Issue / verify credential** — every wallet has both flows. authbound is ⚠️ on both: the flows are
complete up to the accept/share tap, but the wallet's auth gate has never let the happy path render,
so `credential_offer_page.py` and `verification_request_page.py` still hold `TODO:` locators.

**Count credentials** — seven different implementations (list cards / parsed label / page-source text
count / separate tab). authbound is ⚠️ (placeholder locator → always returns 0). procivis is the only
outright ❌: its `HomePage` has just `wait_until_loaded()`.

**Assert the count changed** — the actual pass criterion, and it is inconsistent. gataca, toppan and
unime hard-assert an increase. authbound, heidi, hovi and paradym compute `count_before`/`count_after`
and only **log** the delta — so a no-op issuance passes. procivis asserts nothing at all.

**Open / review a credential's detail** — the "check credential" capability. Only gataca has it
(`CredentialDetailPage`, heading "Credential details"). heidi is ⚠️: it navigates to the credential
*list* screen to read a count label and immediately backs out — no detail view, no field inspection.
Nobody can currently assert *what* was issued (claims, issuer, validity), only *how many*.

**Delete a single credential / wipe all** — gataca only: detail → trash → "Yes, delete" → system
biometric, wrapped by `cleanup_flow.prune_credentials()` and `tests/test_cleanup.py`, forced to run
last. Every other wallet accumulates credentials across runs forever, which inflates counts and
changes what verifiers match against.

**Detect app error screens** — implemented for six wallets, in varying depth: paradym and toppan run
staged `check_for_error()` between every step (plus crash/ANR overlay detection and `[no_retry]`
tagging), heidi has two distinct error screens checked before *and* after sharing, gataca has an error
page plus a separate "Rejected" screen and a backend "Service currently unavailable" dialog, procivis
reports process-screen failures and dumps all visible text, authbound reports `content_error_root`
text. unime and hovi have **none** — a failure there surfaces only as "returned to home" or a bare
timeout.

**Manipulate wallet settings** — heidi enables Show Metadata and sets "Always Ask" for trusted and
untrusted connections; paradym enables Development Mode. Both run from their wallet conftest, before
tests. No other wallet touches settings.

**Collect in-app debug logs** — paradym only (`settings_flow.collect_debug_logs`, via the share sheet).
Everything else relies on the shared logcat/Appium capture.

**Wallet-specific pre-test setup** — gataca only: `setup_flow.ensure_did()` before every test, because
the wallet resets its active DID to `did:gatc:` on each cold start.

---

## Shared capabilities (🔁 — same for every wallet)

Provided by the root `conftest.py`, `base/conftest_helpers.py` and `base/android.py`:

- Screenshot on failure, XML page-source dump on failure (per `reporting` config)
- Per-test screen recording (`recordings/*.mp4`), session-wide logcat (`app.log`), per-test Appium
  server log (`appium.log`), pytest HTML report, `test.log`
- Automatic retry/rerun of failed tests (except cases tagged `[no_retry]`)
- Provider reachability precheck before each case
- User-style app close between tests (recents "Clear all", else swipe the card away — never
  force-stop), followed by navigate-back-to-home
- System-level handling helpers: biometric prompt (fingerprint inject or PIN), permission dialogs,
  ANR/crash overlay detection

---

## Gaps, in the order they cost us most

1. **Counting: procivis has none, authbound returns a hard-coded 0.** Until these exist, "did the
   credential arrive?" is unanswerable for two wallets.
2. **Assertion strength: 4 log-only + 1 nothing.** Unifying this will turn currently-green cases red.
   That's the point — those greens are not evidence of anything today.
3. **Reviewing a credential — only gataca.** No wallet except gataca can check *what* landed. If the
   unified scenario is to assert on issued content (claims/issuer), this needs a detail page per wallet.
4. **Delete / cleanup — only gataca.** Without it, wallet state drifts monotonically across runs, and
   verification results depend on accumulated history rather than the credential just issued.
5. **Error detection missing in unime and hovi.** Both fail as timeouts with no diagnostic, so triage
   means watching the recording.
6. **Onboarding blocked for authbound and gataca** (server-side registration). Not a code gap — tracked
   here so it isn't mistaken for one.
