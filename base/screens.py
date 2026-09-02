"""Which outcome screens a wallet has, declared per wallet and injected into shared code.

The shared wait loop has to ask "is the wallet showing an error?" without knowing what an error
looks like in this wallet. Each wallet answers with its own probes, in one place, so the loop can
be written once.

**Copy the contract, never the file.** Dropping hovi's `error_page.py` into another wallet produces
a module whose XPath matches nothing, whose `present()` returns False forever, and which therefore
disables that wallet's error detection without failing anything. A wallet either supplies its own
probe or says out loud that the screen does not exist.

Three states, not two
---------------------

`None` is a claim: "this wallet has no such screen." `UNKNOWN` is the absence of a claim: nobody has
looked. Collapsing them would republish ignorance as fact — and `UNKNOWN` has to be the **default**,
or a wallet that simply forgot a field silently claims the screen does not exist. Forgetting is
noisy; disclaiming is deliberate and takes a keystroke.
"""
from dataclasses import dataclass, fields
from typing import Any, Callable, List, Optional, Union

# Every probe has the same shape, which is also the shape the wallets' existing page modules
# already expose: `on_screen(driver, timeout=...)` / `present(driver, timeout=...)`.
Probe = Callable[[Any, float], bool]


class _Unknown:
    """Nobody looked. Distinct from None, which claims 'this wallet has no such screen'."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()

Declared = Union[Probe, None, _Unknown]

_OPTIONAL = ("error", "processing", "no_match", "success")


@dataclass(frozen=True)
class Screens:
    """One wallet's outcome screens, plus the two timings that are genuinely per-wallet.

    Frozen and typed rather than a dict so a misspelled key fails loudly at import instead of
    silently disabling a screen for the life of the branch.
    """

    name: str
    home: Probe

    # Optional surfaces. Omit = UNKNOWN (not investigated); write None to claim it does not exist.
    error: Declared = UNKNOWN
    processing: Declared = UNKNOWN
    no_match: Declared = UNKNOWN
    success: Declared = UNKNOWN

    # Reads the wallet's own error copy, so a failure quotes what the wallet said rather than our
    # paraphrase of it. hovi has `error_page.message`; procivis can drill into Code/Message/Cause.
    # A wallet with an error probe but no reader still reports `rejected`, just less specifically.
    error_text: Optional[Callable[[Any], str]] = None

    # How long a glimpse of home is ignored before it counts as "returned home without offering".
    # Per-wallet because it describes the wallet's own behaviour: hovi can flash home while it works
    # on a deeplink, so 5s; a wallet that never does should use 0.0 and get a faster verdict.
    home_grace: float = 0.0

    # How much extra time a visible spinner may buy, in total. authbound's issuance genuinely takes
    # minutes behind a "Please wait", so waiting is progress; hovi's spinner grants nothing.
    processing_grace: float = 0.0

    # hovi's error surface is a persistent banner rather than a screen, so a banner already up when
    # a case starts says nothing about that case. Only hovi sets this, and it is suspected to be an
    # ordinary timed toast (see wallets/hovi/pages/error_page.py) — do not adopt it elsewhere
    # without timing the surface on-device first.
    error_sticky: bool = False

    def probe(self, field_name: str) -> Optional[Probe]:
        """The probe for `field_name`, or None when it is absent *or* unmapped.

        Callers that only need "can I check this?" use this. Callers that must distinguish a claim
        of absence from ignorance — failure messages — read the attribute directly.
        """
        value = getattr(self, field_name)
        return value if callable(value) else None

    def unmapped(self) -> List[str]:
        """Optional screens nobody has investigated, so a failure message can admit it.

        The difference this preserves: unime reporting "no error screen appeared" when its error
        surface has never been looked for is a lie; "unime has no mapped error screen" is not.
        """
        return [f.name for f in fields(self)
                if f.name in _OPTIONAL and isinstance(getattr(self, f.name), _Unknown)]
