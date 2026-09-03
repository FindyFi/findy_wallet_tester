# Wallets

One directory per wallet under test, plus `example/` as the template to copy when adding a new one.
Each wallet directory has the same shape:

```
<wallet>/
├── config.json     # package, activity, PIN, timeouts — this wallet only
├── conftest.py     # per-wallet fixtures (home-screen setup, teardown, wallet-specific hooks)
├── pages/          # Page Object Model classes — locators live here
├── flows/          # multi-step user flows (init / credential / verification / …)
└── tests/          # test files, collected by pytest and by runners/run_tests.py
```

The issuers and verifiers are **not** here: every wallet is tested against the same set, so they
live once in [`config/providers.json`](../config/providers.json) and `.env` chooses which of them a
run exercises. `config.json` holds only what is genuinely specific to this wallet.

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

A cell goes ✅ only once a run has exercised it, never on the strength of code that looks right.
That is also the merge bar: nothing lands until a run has proven it. ⚠️ covers the gap between
"written, locators captured live" and "seen working". These results get published, so keep the two
apart.

Also re-read the whole matrix at the start of any unification work: it is the checklist of what has to
become uniform.

---

## Capability matrix

What the test suite can actually **do** with each wallet today. Every ❌/⚠️ is either a gap to close or
a difference to justify. Status as of 2026-08-20, branch `fix/hovi_general`.

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
| Onboard from fresh install            |    ❌     |   ❌   |  ✅   |  ✅  |   ✅    |    ✅    |   ⚠️    |  ✅   |
| Open / unlock returning wallet        |    ✅     |   ✅   |  ✅   |  –   |   ✅    |    ✅    |   –    |  ✅   |
| Reset wallet (wipe + re-onboard)      |    ⚠️      |   ⚠️    |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Clean-slate strategy                  |  delete   | delete |  wipe | wipe |  wipe   |   wipe   |  wipe  | wipe  |
| **Credentials**                       |           |        |       |      |         |          |        |       |
| Issue credential (deeplink -> accept) |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Verify credential (deeplink -> share) |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ✅   |
| Count credentials                     |    ✅     |   ⚠️    |  ✅   |  ✅  |   ✅    |    ⚠️     |   ✅   |  ✅   |
| **Assert** the count changed          |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ⚠️     |   ✅   |  ✅   |
| Open / review a credential's detail   |    ⚠️      |   ✅   |  ⚠️    |  ⚠️   |   ❌    |    ❌    |   ❌   |  ❌   |
| Delete a single credential            |    ✅     |   ✅   |  ❌   |  ✅  |   ❌    |    ❌    |   ❌   |  ❌   |
| Wipe all credentials (post-suite)     |    ✅     |   ✅   |  ❌   |  ✅  |   ❌    |    ❌    |   ❌   |  ❌   |
| **Support**                           |           |        |       |      |         |          |        |       |
| Detect app error screens              |    ✅     |   ✅   |  ✅   |  ✅  |   ✅    |    ✅    |   ✅   |  ⚠️    |
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
reports the build as of the moment the run started. The same dump also yields every URL scheme the
package registers an intent filter for, recorded alongside the version. That is the evidence behind
the `unroutable` diagnosis described under error detection, and it will show a wallet quietly
dropping a scheme in a new build.

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

**Clean-slate strategy** — every wallet is meant to start a run holding nothing. A verification that
passes by presenting a credential left over from an earlier run is not evidence about today's issuer.
"Clean slate" covers two different mechanisms, and the row above says which one a wallet gets.

- **wipe** (heidi, hovi, paradym, procivis, toppan, unime): the reset above, once per session. It
  clears keys, settings and consent state, not only credentials. toppan qualifies despite having no
  onboarding at all, because its `clearApp` path lands straight back on home.
- **delete** (authbound, gataca): the per-credential UI delete path. Both need manual email/server
  registration, so a wipe would brick them, and a rerun must never need a human.

The wipe is not on by default. `onboarding.skip_if_done` is still `true` in every wallet config, and
it becomes a locally toggleable `.env` setting in the unification work, so until then the policy is
written down here but not in force. The UI delete path stays even for wallets that can be wiped,
because sometimes a cleanup is wanted rather than a start-from-scratch and no test cares how the
wallet got clean.

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

**Counting rendered cards saturates, and gataca proves it.** Measured 2026-09-03: a gataca prune
deleted **six** credentials while `count_credentials()` read `3, 3, 3, 3, 3, 2, 1`. It counts card
elements inside a ScrollView (`home_page.py:59`), and Android only materialises the cards that are
laid out, so the number is capped by what fits on screen — roughly 3 here.

The prune survives it (the count only saturates upward, so the loop still stops at the right place),
but **issuance measurement does not**. The same session ran `procivis_issuer → gataca` twice: at 3
credentials it read `3 → 3, +0` and failed, and immediately after pruning to the floor it read
`1 → 2, +1` and passed. Same code, same issuer, opposite verdicts — the first was a false red caused
purely by a dirty wallet. It fails closed, which is the safe direction, but any gataca issuance
result taken on a non-empty wallet is worthless.

This is a risk for **every wallet that counts card elements** — gataca, hovi, unime and toppan. hovi
was probed for it (its count did not grow when the list was scrolled) but at only two credentials,
which cannot show a ceiling of three. The fix is to scroll and accumulate unique cards, or find a
wallet-reported total; until then, clean-slate is not a tidiness preference for these wallets, it is
what makes their numbers mean anything.

**Assert the count changed** — the actual pass criterion. Re-measured 2026-09-03: **all eight now
assert**, where on 2026-08-20 only four did. gataca, heidi and paradym used to compute
`count_before`/`count_after` and merely log the delta, so a no-op issuance passed; procivis asserted
nothing at all. heidi, paradym and procivis now go through `base.credential_count.assert_increased`,
and the run evidence is in the logs (`heidi: 17 → 18, +1`, `paradym: 9 → 10, +1`). procivis is ⚠️
only because its counting has not yet appeared in a run, not because it does not assert.

hovi is the case that proves why this matters. It passed three issuance tests on `2 → 2, +0` on both
2026-08-12 and 2026-08-19, green for a week with nothing arriving. A probe confirmed the count was
truthful (the locator matched exactly the cards present, and did not grow when the list was
scrolled), so the flat count was real and only the missing assertion hid it.

The 2026-08-20 runs closed it out. With the wallet wiped, `hovi_issuer` stored a credential on every
run, so hovi can hold what our issuers hand out. The two that still fail were never silent either:
`authbound_issuer` and `sphereon_issuer` accept the offer and then raise the error banner, which
nothing was checking. Both now report `rejected`, and `not_stored` no longer fires for hovi at all.
Re-measure rather than trusting this line — it has been wrong in both directions, most recently by
calling gataca unasserted when `assert added > 0` had been sitting in its test all along. The reason
it keeps going stale is that there are **three spellings** of the same assertion, so a grep for any
one of them under-reports:

```bash
grep -lE "assert_increased|assert count_after|assert added" wallets/*/tests/test_credential_issuance.py
```

**Open / review a credential's detail** — the "check credential" capability. Only gataca has it in
full (`CredentialDetailPage`, heading "Credential details"). heidi is ⚠️: it navigates to the
credential *list* to read a count label and immediately backs out. authbound is ⚠️ for the opposite
reason — its detail screen is reachable and captured (see below) and does expose the raw claims
(`exp`, `iat`, `jti`, `nbf`, `sub`, expandable Pension/Person groups, ISSUER), but the page object
only uses it to delete; nothing reads a field. So still no wallet can assert *what* was issued
(claims, issuer, validity), only *how many*.

**Delete a single credential / wipe all** — gataca, hovi and authbound. authbound closed on
2026-09-03: pruned 2 → 1 → 0 and, separately, 1 → 0, with the final count asserted each time. hovi was exercised on 2026-08-20, pruning a wallet holding two credentials down to
empty and re-counting between deletions. Two of its paths have still never occurred in a run, so
they are not claimed here: `can_delete()` returning False, and the `DeleteRefused` restart-and-retry.

**The loop is shared; only the gestures below are per-wallet.** All three had written the same
count → open → delete → re-count loop independently and agreed on every structural decision, so it
now lives once in `base/cleanup.py` and each wallet's `flows/cleanup_flow.py` supplies gestures
only. Two bounds are stated there rather than in eight places: a delete that reports success while
leaving the card in place is stopped by `max_deletions` (without it the loop is infinite, because
the count never falls), and an **unreadable count stops the prune rather than deleting blind** —
leaving a wallet dirty is recoverable, deleting from a wallet whose contents we cannot see is not.
`base/tests/test_cleanup_loop.py` pins both with fake gestures and no device.

That sharing also fixed a bug worth remembering when adding the next wallet: hovi had defined its
own `DeleteRefused(RuntimeError)` with the same name as the shared one, so the exception it raised
was **never the class the loop catches**, and its restart-and-retry was dead code — on precisely the
intermittent failure nobody watches closely. Import the exception from `base.cleanup`; do not
declare another.

The five wallets with no cleanup at all (heidi, paradym, procivis, toppan, unime) need locators
captured live: **nothing in the 792 archived XML dumps touches a delete path**, because dumps come
from issuance and verification, which never open a detail screen. Two of them are visibly paying for
it — heidi reached 19 credentials and paradym 10 on 2026-09-02, against a clean-slate target of 0.

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

- **hovi**: Home → tap a card → hovi expands it **in place** (no navigation: the "Credentials"
  heading is replaced by "Credential Details" and its claims, and a `Done` button plus an unlabelled
  delete icon appear) → the icon opens a "Delete Credential?" dialog with Cancel/Delete → back on
  Home. No authentication, and no protected credential. Nothing here carries a resource-id, so
  clickable controls are matched on `content-desc` and headings on `text`; the delete icon has only a
  private-use glyph from hovi's icon font, so `can_delete()` reports False rather than tapping the
  wrong control if a wallet update changes that font. `cleanup.max_credentials` is 0. It was
  briefly 2, to spare two EWC credentials no configured provider could re-issue, but the wallet is
  meant to start every run empty and `hovi_issuer` has since been seen storing a credential, so
  there is nothing left to protect.

All three are wrapped by `flows/cleanup_flow.prune_credentials()` and `tests/test_cleanup.py`, which
the root conftest's `_MODULE_ORDER` already sorts last for every wallet, so no marker is needed
(gataca has one only because its DID grouping would otherwise reorder the suite). The other five
wallets accumulate
credentials across runs forever, which inflates counts and changes what verifiers match against.

**Detect app error screens** — implemented for six wallets, in varying depth: paradym and toppan run
staged `check_for_error()` between every step (plus crash/ANR overlay detection and `[no_retry]`
tagging), heidi has two distinct error screens checked before *and* after sharing, gataca has an error
page plus a separate "Rejected" screen and a backend "Service currently unavailable" dialog, procivis
reports process-screen failures and dumps all visible text, authbound reports `content_error_root`
text. hovi gained detection on 2026-08-19 and it fired on real cases the next day. Its only error
surface is a banner reading "Please check if the QR is correct and try again", plus a processing
screen the accessibility tree renders as a lone `Cancel` button. Its flows now tell both apart from
"no screen at all", which they previously all reported as
`Element ('xpath', '//*[@text="Accept"]') not found`.

Note it is a banner rather than a screen: a strip pinned to the top, overlaying home, an empty
wallet or the spinner alike, matched on its copy because nothing structural identifies it. How long
it stays up is unsettled. It was once seen still up minutes later, so the flows compare presence
before and after firing a deeplink and count only a newly appeared banner, clearing a leftover one
by restarting the app. That clearing path has never actually run, which suggests it expires on its
own during teardown. The guard costs one poll and stays until someone times it on the device.

unime has no error detection at all, so a failure there surfaces as "returned to home" or a bare
timeout.

hovi is also the first wallet to name its outcomes rather than describe them, in
`wallets/hovi/flows/outcome.py`: `success`, `rejected`, `processing`, `dismissed`, `absent`,
`unroutable` and `no_match` from the flow, plus `not_stored` and `nothing_to_present` from the
tests. Failures carry the name as a `category` attribute and as a `[tag]` on the message. The other
seven wallets are meant to adopt the same names, which is what the published report needs before a
red cell can say *why*. Six of the nine fired on real cases in the 2026-08-20 runs; `dismissed` and
`processing` have never occurred.

Two of the names exist because the first run showed that reaching a screen is not the same as the
screen being usable. Each flow now confirms before it acts, and again after:

- `no_match`: hovi renders "No Credential Found / The credential is not present in your wallet"
  *inside* the request screen, so the heading and the refusal sit in the tree together.
  `procivis_verifier` hit this holding a credential, and because only the heading was checked, the
  flow waited out its timeout on an `Accept` button that was never coming. Different from
  `nothing_to_present`, which means an empty wallet. One is a credential-type disagreement between
  verifier and wallet, the other is issuance having failed earlier in the run.
- `rejected` after a step that succeeded: `authbound_issuer` and `sphereon_issuer` both showed the
  offer, took the Accept, then raised the error banner. Nothing looked, so the run concluded
  `not_stored`. True, but silent about the reason the wallet had already put on screen.

**Deeplinks are never rewritten or refused.** Whatever URL a provider publishes is fired exactly as
given, because what a wallet does with the real URL is the result we are here to record. When
nothing comes forward, `flows/deeplink.py` asks the device which schemes the wallet actually
registers and reports `unroutable` when that explains it. `get_app_info` parses those out of the
same `dumpsys package` dump it already reads for the version, and every run records them in
`app_info.json`. Diagnosis after the fact, never a gate before it, and never a claim without a dump
behind it.

authbound and gataca still contradict this. Both rewrite paradym's
`https://paradym.id/invitation?...` into `openid4vp://`, which is why paradym looks green in their
columns and red in hovi's for the same behaviour. Removing those rewrites is part of the unification
work.

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

1. **Assertion strength: 3 log-only (gataca, heidi, paradym) + 1 nothing (procivis).** Four of eight
   wallets still pass an issuance test with no evidence a credential landed. Closing the remaining
   ones will turn currently-green cases red. That is the point. Those greens are not evidence of
   anything today. hovi was the fourth until 2026-08-19, and it is the worked example: three of its
   tests passed on `+0` for at least a week.
2. **Counting: procivis has none.** Until it exists, "did the credential arrive?" is unanswerable
   there. authbound closed this on 2026-08-13.
3. **Reviewing a credential: nobody asserts on content.** gataca and hovi can both *open* a
   credential and see its claims, and authbound's detail screen exposes them too, but no wallet
   checks a single field. If the unified scenario is to assert on issued content (claims/issuer),
   the remaining five need a detail page and all eight need the assertions.
4. **Delete / cleanup: gataca and hovi, plus authbound pending its first run.** For the other
   five, wallet state drifts monotonically across runs, and verification results depend on
   accumulated history rather than the credential just issued.
5. **Error detection missing in unime.** Its failures surface as "returned to home" or a bare
   timeout, so triage means watching the recording. hovi was in the same position until 2026-08-19;
   the lesson from fixing it is that a single blocking wait on the accept button reports every
   distinct failure (app never foregrounded, wallet still processing, wallet showed an error) as
   one phantom "locator not found".
6. **Onboarding blocked for authbound and gataca** (server-side registration). Not a code gap — tracked
   here so it isn't mistaken for one.
