"""What a flow knows about the case it is running, passed to shared code instead of re-derived.

The shared wait loop and the interstitial handlers need scattered facts — which wallet, which
credential, the PINs, the page kwargs — and every one of them used to be a parameter threaded
through another signature. hovi's `outcome.raise_for` had grown to nine positional arguments before
this existed.

Mutable on purpose. `notes` collects evidence *during* a wait that only matters if the wait later
fails: authbound's biometric-enrollment offer is the case that forced it — seeing the enrollment
wizard says nothing while the flow is still progressing, but it is the whole explanation if the
flow then times out.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Set


@dataclass
class FlowContext:
    flow: str                       # "credential_flow" / "verification_flow" — message prefix
    wallet: str                     # the wallet's directory name, used in every message
    what: str = ""                  # the credential or test case being exercised
    app_package: str = ""
    url: str = ""                   # the deeplink actually fired, for the unroutable diagnosis
    pin: str = ""                   # the wallet's own passcode
    device_pin: str = ""            # the device lock PIN, for system auth prompts
    page_args: Dict[str, Any] = field(default_factory=dict)
    notes: Set[str] = field(default_factory=set)
    prompt_budget: Dict[str, int] = field(default_factory=dict)

    # How many times each interstitial fired during the last wait. Written by outcome.wait_for and
    # read by outcome.raise_for, so a `prompt_loop` failure can name the prompt that looped rather
    # than saying "a prompt" — the caller never has to thread it between the two.
    fired: Dict[str, int] = field(default_factory=dict)

    @property
    def timeouts(self) -> dict:
        return self.page_args.get("timeouts", {})

    def timeout(self, key: str, fallback: float = 30.0) -> float:
        """The wallet's timeout for `key`, falling back to its default, then to `fallback`."""
        t = self.timeouts
        return t.get(key, t.get("default", fallback))
