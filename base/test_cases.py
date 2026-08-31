"""Builds the issuance / verification parametrize lists every wallet's tests share.

These ran as eighteen near-identical copies, one per wallet per flow, and had drifted: seven of the
nine issuance modules honoured a case's `xfail` and only four of the nine verification modules did,
so the same config key meant different things depending on which wallet read it.

Called at **collection** time, which is why the loading lives in `base/config.py` rather than the
root conftest — see the note there.
"""
from typing import List

import pytest

from base.config import provider_matrix

# A credential's "type" and the .env list that selects its providers are spelled differently:
# a case is an issuance, the thing that serves it is an issuer.
_SELECTION_VAR = {"issuance": "ISSUERS", "verification": "VERIFIERS"}


def _cases(wallet: str, kind: str) -> List:
    """Every configured case of one kind for one wallet, as pytest params.

    The id is `<agent>/<credential>` (e.g. "hovi_issuer/credential_issuance"). The compact report
    parses that back out to identify a matrix row, so it is a published interface, not a label.

    A case may carry an `xfail` in the registry whose value is the reason. Non-strict, so a
    provider that starts working shows up as an unexpected pass rather than a new failure.
    """
    return [
        pytest.param(
            agent, credential_name,
            id=f"{agent}/{credential_name}",
            marks=[pytest.mark.xfail(reason=credential["xfail"], strict=False)]
            if credential.get("xfail") else [],
        )
        for agent, agent_config in provider_matrix(wallet).items()
        for credential_name, credential in agent_config.get("credentials", {}).items()
        if credential.get("type") == kind
    ] or [
        # Reachable on purpose: selecting no providers is a supported .env setting, so say that
        # rather than leaving a bare empty parametrize, which pytest reports as a collection error.
        pytest.param("", "", marks=pytest.mark.skip(
            reason=f"No {kind} providers selected for {wallet} — see "
                   f"DEFAULT_{_SELECTION_VAR[kind]} / {wallet.upper()}_{_SELECTION_VAR[kind]} "
                   "in .env"
        ))
    ]


def issuance_cases(wallet: str) -> List:
    return _cases(wallet, "issuance")


def verification_cases(wallet: str) -> List:
    return _cases(wallet, "verification")
