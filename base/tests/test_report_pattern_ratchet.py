"""The report's regex fallback is a contract on message prose, and it may only shrink.

`REASON_PATTERNS` in `generate_compact_report.py` reads a failure category out of an exception's
wording when a run has no typed category. Every wallet but gataca now raises typed failures, so
what is left matters for archived runs and for gataca until it is converted. This ratchet keeps
it that way: every entry carries exactly one tag (`# third-party`, `# old runs`,
`# pending: <wallet>`), a pending wallet that has gained a `screens.py` must be re-tagged or
deleted, and the set of entries standing for our own prose cannot grow.

The tags are read from the source with `ast` and `tokenize`, not from the runtime tuple, because
comments do not survive into it.
"""
import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

import generate_compact_report as report

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "generate_compact_report.py"
TAG_RE = re.compile(r"^#\s*(third-party|old runs|pending: [a-z_]+)\s*$")

# The archive's needs as measured on 2026-11-12 (all 309 runs archived in the checkout it was
# written in): the seven entries tagged `# old runs` plus the five still pending. Growing this
# set means our own prose became a contract again; type the failure at the raise site instead.
OLD_RUN_PATTERNS = frozenset({
    r"No matching credentials",
    r"does not have the cards required",
    r"returned to (?:its )?home (?:screen )?without",
    r"error dialog",
    r"error screen",
    r"Credential issuance failed",
    r"Verification failed for",
    r"Active DID is ",
    r"No offer/error/home screen after",
    r"No success/home screen after",
    r"was rejected",
    r"^assert\s",
})


def _entries():
    """(lineno, (category, pattern), tags) for each element of the REASON_PATTERNS literal."""
    text = SOURCE.read_text()
    tree = ast.parse(text)
    elts = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "REASON_PATTERNS" for t in node.targets):
            elts = node.value.elts
    assert elts is not None, "REASON_PATTERNS is no longer a module-level assignment"
    comments = {}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            comments.setdefault(tok.start[0], []).append(tok.string)
    out = []
    for elt in elts:
        tags = [c for line in range(elt.lineno, elt.end_lineno + 1)
                for c in comments.get(line, []) if TAG_RE.match(c)]
        out.append((elt.lineno, ast.literal_eval(elt), [TAG_RE.match(t).group(1) for t in tags]))
    return out


ENTRIES = _entries()


def test_the_source_literal_matches_the_runtime_tuple():
    """The tags are read from the source, so the source must be what runs."""
    assert len(ENTRIES) == len(report.REASON_PATTERNS)
    for i, (lineno, literal, _) in enumerate(ENTRIES):
        assert literal == report.REASON_PATTERNS[i], f"entry {i} (line {lineno}) differs at runtime"


def test_every_entry_carries_exactly_one_tag():
    """An untagged entry has no stated reason to exist; two tags contradict each other."""
    for lineno, (category, pattern), tags in ENTRIES:
        assert len(tags) == 1, (
            f"REASON_PATTERNS entry {category!r}/{pattern!r} at line {lineno} has tags {tags}; "
            "it needs exactly one of '# third-party', '# old runs', '# pending: <wallet>'"
        )


def test_no_pending_tag_names_a_wallet_that_has_typed_its_failures():
    """A wallet with a screens.py raises typed failures, so its pending entry is dead weight."""
    for lineno, (category, pattern), tags in ENTRIES:
        for tag in tags:
            if not tag.startswith("pending: "):
                continue
            wallet = tag.split(": ", 1)[1]
            assert (ROOT / "wallets" / wallet).is_dir(), (
                f"line {lineno}: '# pending: {wallet}' names no directory wallets/{wallet}/"
            )
            assert not (ROOT / "wallets" / wallet / "screens.py").exists(), (
                f"line {lineno}: wallet {wallet!r} now has a screens.py, so {pattern!r} is no "
                "longer pending: re-tag it '# old runs' (needed by archived runs) or delete it"
            )


def test_the_old_run_patterns_only_shrink():
    """The snapshot above is the archive's needs; an entry outside it is new prose as contract."""
    ours = {pattern for _, (_, pattern), tags in ENTRIES
            if tags and (tags[0] == "old runs" or tags[0].startswith("pending:"))}
    assert ours <= OLD_RUN_PATTERNS, (
        f"new entries for our own messages are not accepted, type the failure at the raise site: "
        f"{sorted(ours - OLD_RUN_PATTERNS)}"
    )


def test_every_category_is_one_the_report_knows():
    """A category outside FAILURE_CATEGORIES would render as a plain red cell."""
    for _, (category, _), _ in ENTRIES:
        assert category in report.FAILURE_CATEGORIES, category


def test_each_entry_is_one_alternative():
    """One alternative per entry keeps each tag true of exactly the text it covers."""
    for lineno, (_, pattern), _ in ENTRIES:
        assert "|" not in pattern, f"line {lineno}: {pattern!r} holds more than one alternative"


@pytest.mark.parametrize("headline", [
    "RuntimeError: Credential offer screen ('OID4VCI Issuance') did not appear within 30s"
    " — check that the deeplink was handled by the app",
    "RuntimeError: [credential_flow] Could not deliver 'x': something",
    "RuntimeError: Credential acceptance did not complete within timeout",
])
def test_the_deleted_alternatives_no_longer_classify_untyped_prose(headline):
    """These were our own wording; typed failures carry their category instead."""
    assert report.classify_failure("E   " + headline + "\n")[0] is None


def test_a_typed_failure_for_a_deleted_shape_is_still_classified_by_its_tag():
    """Deleting the prose pattern costs nothing once the failure is typed."""
    log = ("E   base.outcome.FlowFailure: [unroutable] [credential_flow] "
           "Could not deliver 'x': boom\n")
    assert report.classify_failure(log)[0] == "unroutable"


@pytest.mark.parametrize("headline, expected", [
    ("Error: Activity not started, unable to resolve Intent { act=android.intent.action.VIEW"
     " dat=https://paradym.id/... flg=0x10000000 pkg=com.gataca.identity }", "unroutable"),
    ("selenium.common.exceptions.TimeoutException: Message:", "processing"),
    ("RuntimeError: [credential_flow] No offer/error/home screen after 30s for"
     " 'credential_issuance'", "processing"),
    ("AssertionError: Active DID is '', expected 'JWK Identity' for did_method 'jwk'", "setup"),
    ("RuntimeError: [verification_flow] Credential verification was rejected"
     " (wrong DID method?)", "rejected"),
    ("RuntimeError: [after deeplink] Paradym error screen. Reason: Credential information"
     " could not be extracted", "rejected"),
    ("RuntimeError: [credential_flow] Credential issuance failed for 'credential_issuance':"
     " An error occurred.", "rejected"),
    ("RuntimeError: Wallet does not have the cards required by 'pension_verification'"
     " — 'UNAVAILABLE CARDS' shown.", "nothing_to_present"),
    ("RuntimeError: [verification_flow] App returned to home without showing a consent screen"
     " for 'pension_verification'", "dismissed"),
    ("assert 2 > 2", "not_stored"),
])
def test_the_kept_patterns_still_classify_real_archived_headlines(headline, expected):
    """One real headline per tag kind, so trimming the tuple cannot silently lose a reading."""
    assert report.classify_failure("E   " + headline + "\n")[0] == expected
