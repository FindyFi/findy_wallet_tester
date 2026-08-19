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
a difference to justify. Status as of 2026-08-17, branch `main`.

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
| Issue credential (deeplink -> accept) |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Verify credential (deeplink -> share) |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Count credentials                     |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ❌    |   ✅   |  ✅   |
| **Assert** the count changed          |    ✅     |   ⚠️    |  ⚠️    |  ⚠️   |   ⚠️     |    ❌    |   ✅   |  ✅   |
| Open / review a credential's detail   |    ⚠️      |   ✅   |  ⚠️    |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
| Delete a single credential            |    ⚠️      |   ✅   |  ❌   |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
| Wipe all credentials (post-suite)     |    ⚠️      |   ✅   |  ❌   |  ❌  |   ❌    |    ❌    |   ❌   |  ❌   |
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

**Open / unlock returning wallet** — five different mechanisms: app passcode (authbound), device PIN via
the system biometric prompt (gataca), biometric only (heidi), app PIN (paradym, procivis), password
(unime). hovi and toppan have no lock to open.

**Reset wallet** — `init_flow.run(skip_if_done=False)` clears app data and re-onboards; triggered once
per session by `conftest_helpers.navigate_to_home` when `onboarding.skip_if_done` is false. authbound
and gataca are ⚠️: they clear the data and then raise, because they can't re-onboard — so using
`skip_if_done=false` on those two leaves the wallet unusable until someone registers it by hand.

**Issue / verify credential** — every wallet has both flows, and all eight now complete both for at
least one counterparty. authbound was the last ⚠️ and closed on 2026-08-13:

- *Issuance* runs end to end against `authbound_issuer`: consent screen (ISSUANCE REQUEST → "Add"),
  device authentication, success screen, and the wallet's own total goes up. The old blocker was a
  device with no fingerprint enrolled, which makes Android open its *enrollment* wizard instead of an
  auth prompt. Once one is enrolled the prompt carries a **PIN fallback**, which `base/android.py`
  answers — no fingerprint is ever simulated on a physical device. `requires_fingerprint: true` in the
  wallet's config asserts that precondition and fails fast with instructions if it goes missing.
  (This supersedes two earlier diagnoses — an auth/profile gate rejecting offers, and enrollment being
  unautomatable. Both are resolved.)
- *Verification* runs end to end against `authbound_verifier`: request screen, share, and a complete
  `request.jwt` → `direct_post` round trip. `verification_request_page.py` holds real captured
  locators; its `TODO:` placeholders are gone.

The wallet demands roughly **one authentication per document it presents** — seven for six documents,
measured 2026-08-12 — so `document_success_page.wait_for_outcome()` answers repeat prompts up to a cap
instead of assuming a single one. That page is shared by both flows: the same success screen and ids
serve issuance and presentation, only the header copy differs.

The other counterparties fail, and these are *results*, not defects: waltid returns 404 for the
spec-required `.well-known/openid-credential-issuer/<path>` metadata, while hovi, procivis, sphereon and
paradym serve valid metadata and offers that the wallet then rejects internally
(`issueDocumentsFromOffer failure`). Both are readable straight from `app.log`, which carries the
wallet's full HTTP traffic under the logcat tag `EUDI Wallet PROD-RELEASE`.

**Count credentials** — the mechanism is per-wallet and stays that way: gataca, hovi and unime count
card elements (with locators that have nothing in common), heidi, paradym and authbound parse a count
label, toppan counts the card containers in its WebView list. heidi and authbound navigate to get it —
to a list screen and the Wallet tab respectively — and both return to where they started.

One lesson worth carrying to the other card-counting wallets: **count containers, never text inside
them.** Toppan counted an "Issued on" line per card until 2026-08-05, when a saved page dump showed 14
card containers but only 11 of those lines — Chrome prunes the descendants of cards below the scroll
viewport, so the count saturated and every issuance looked like it stored nothing. Any wallet whose
credential list outgrows the accessibility tree will read low; keeping wallets trimmed is what keeps
counting honest. What *is* uniform is the contract, in `base/credential_count.py`: every wallet returns a real
number or raises `CredentialCountUnavailable`, verifies its screen before believing a count, and
leaves the app where it found it. No wallet returns a silent 0 any more, so "wallet is empty" and
"my locator broke" are finally different answers.

**authbound** ✅ since 2026-08-13 — it switches to the Wallet tab and back (the second wallet after
heidi where counting is a navigation step) and reads the wallet's *own* header total, "Wallet · 4",
rather than counting cards: its document cards are `android.view.View` with no resource-id inside a
scrolling list, so the header is both simpler and immune to the truncation described above. An empty
wallet omits that label and says "Your wallet is empty", which is a real 0.

**procivis** ❌ is the only wallet left with no counting at all — no credential-list locator, its
`HomePage` only knows `wait_until_loaded()`. It does issue credentials successfully, so this is
capturable as soon as someone dumps `WalletScreen` with a credential present.

A second locator lesson, from the authbound fix: **a resource-id you can see in a dump is not
necessarily findable via `AppiumBy.ID`.** Appium's `id` strategy only matches the qualified
`pkg:id/name` form, so bare Compose `testTag`s — which is what authbound's bottom-nav tabs are — resolve
only via XPath on `@resource-id`. authbound's counting logic was correct for months but never ran,
because that first tab click never resolved and `BasePage.click` reports every timeout as "not
clickable", which reads like an overlay or timing problem. When a locator times out, grep the run's
`appium.log`: `no such element` on every retry means not-found, not un-clickable.

**Assert the count changed** — the actual pass criterion, and still the most inconsistent thing here.
Measured 2026-08-17: **authbound, toppan and unime** hard-assert an increase. **gataca, heidi, hovi and
paradym** compute `count_before`/`count_after` and only **log** the delta, so a no-op issuance passes.
**procivis** asserts nothing at all. Re-measure with
`grep -l "assert count_after" wallets/*/tests/test_credential_issuance.py` rather than trusting this
line — it was wrong before (gataca was listed as asserting, and does not).

**Open / review a credential's detail** — the "check credential" capability. Only gataca has it in
full (`CredentialDetailPage`, heading "Credential details"). heidi is ⚠️: it navigates to the
credential *list* to read a count label and immediately backs out. authbound is ⚠️ for the opposite
reason — its detail screen is reachable and captured (see below) and does expose the raw claims
(`exp`, `iat`, `jti`, `nbf`, `sub`, expandable Pension/Person groups, ISSUER), but the page object
only uses it to delete; nothing reads a field. So still no wallet can assert *what* was issued
(claims, issuer, validity), only *how many*.

**Delete a single credential / wipe all** — gataca, plus authbound as of 2026-08-17 (⚠️ — written and
its locators captured live, but not yet exercised by a run; flip to ✅ after one).

- **gataca**: detail → trash → "Yes, delete" → system biometric, and it must preserve the
  self-attested device credential.
- **authbound**: Home dashboard → tap the front **carousel** card → document details →
  `document_details_screen_delete_button` → `…dialogue_delete_document_positive_button`, then
  straight back to the dashboard. Two differences worth knowing: it needs **no authentication at
  all** (confirming is enough — waiting for a biometric or PIN prompt would just time out), and it
  has **no protected credential**, so a target of 0 really does empty the wallet. It deletes the
  front carousel card and re-reads, rather than indexing a list that shifts underneath it — the
  documents list on the Wallet tab has been seen rendering its cards outside the accessibility tree
  entirely, while the carousel card is reliably the single clickable descendant of the only nested
  scrollable on the screen. `cleanup.max_credentials` in the wallet config sets how many to keep.

Both are wrapped by `flows/cleanup_flow.prune_credentials()` and `tests/test_cleanup.py`, which the
root conftest's `_MODULE_ORDER` already sorts last for every wallet — no marker needed (gataca has one
only because its DID grouping would otherwise reorder the suite). The other six wallets accumulate
credentials across runs forever, which inflates counts and changes what verifiers match against.

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

1. **Assertion strength: 4 log-only (gataca, heidi, hovi, paradym) + 1 nothing (procivis).** Five of
   eight wallets pass an issuance test with no evidence a credential landed. Unifying this will turn
   currently-green cases red. That's the point — those greens are not evidence of anything today.
2. **Counting: procivis has none.** Until it exists, "did the credential arrive?" is unanswerable
   there. authbound closed this on 2026-08-13.
3. **Reviewing a credential — only gataca.** No wallet except gataca can check *what* landed. If the
   unified scenario is to assert on issued content (claims/issuer), this needs a detail page per wallet.
4. **Delete / cleanup — gataca and authbound (the latter pending its first run).** For the other six,
   wallet state drifts monotonically across runs, and verification results depend on accumulated
   history rather than the credential just issued.
5. **Error detection missing in unime and hovi.** Both fail as timeouts with no diagnostic, so triage
   means watching the recording.
6. **Onboarding blocked for authbound and gataca** (server-side registration). Not a code gap — tracked
   here so it isn't mistaken for one.
