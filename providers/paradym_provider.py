import logging
from urllib.parse import parse_qs, urlsplit

from providers.web_provider import WebDeeplinkProvider

logger = logging.getLogger(__name__)

# Query parameters that identify which OpenID flow an invitation carries, mapped to the scheme
# that flow is published under. Both are the standard parameter names — `credential_offer_uri`
# is OID4VCI, `request_uri` is OID4VP — which is exactly why unwrapping is safe: only paradym's
# https envelope is non-standard, never its contents.
_FLOW_SCHEMES = (
    ("credential_offer_uri", "openid-credential-offer"),
    ("credential_offer", "openid-credential-offer"),
    ("request_uri", "openid4vp"),
)


class ParadymProvider(WebDeeplinkProvider):
    """Serves paradym's invitations under the scheme every wallet in the fleet registers.

    Paradym publishes both directions wrapped in an https envelope:

        OFFER  https://paradym.id/invitation?credential_offer_uri=https%3A%2F%2F...
        PROOF  https://paradym.id/invitation?request_uri=https%3A%2F%2F...&client_id=...

    `paradym.id` is not an app-link any wallet but paradym's own verifies, so an explicit-package
    VIEW intent carrying that URL matches no intent filter and the wallet never comes forward.
    The case then fails `[unroutable]` — 11 cells across six wallets on 2026-09-07, all one cause.

    Discovery is inherited unchanged from WebDeeplinkProvider (the issuer answers with a bare URL,
    the verifier page carries it in an href); this class only strips the envelope afterwards, so
    there is one paradym code path rather than two.

    **This is not the harness papering over a wallet's gap.** It previously *was*: authbound and
    gataca each rewrote the URL privately in their own flows, so paradym showed green for them and
    red for everyone else, and a harness choice was published as a wallet property
    (project_no_harness_url_rewriting). Doing it once, provider-side, for every wallet equally is
    the symmetric version of that principle, not an exception to it. Those three private rewrites
    are deleted; do not reintroduce one.

    Every wallet under test registers both schemes (checked from each run's `app_info.json`), so
    the unwrapped URL is deliverable everywhere. The published notes should still say that emitting
    a standard scheme — or making paradym.id an app-link — is a small change on paradym's side.

    Config format (config/providers.json):
        "paradym": {
            "type": "paradym",
            "base_url": "https://issuer.paradym.pensiondemo.findy.fi",
            "credentials": {
                "credential_issuance": { "type": "issuance", "path": "pensioncredential.json" }
            }
        }
    """

    def get(self, name: str) -> str:
        return self._unwrap(super().get(name))

    @staticmethod
    def _unwrap(url: str) -> str:
        """Rebuild a paradym https invitation under its OpenID scheme; pass anything else through.

        The whole query string is preserved verbatim. Trimming it to the *_uri parameter looks
        tidy and breaks OID4VP: dropping `client_id` makes wallets reject the request with a
        MissingClientId error.
        """
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or "paradym.id" not in parts.netloc:
            return url

        if not parts.query:
            raise ValueError(
                f"Paradym returned an invitation with no query string ({url[:80]}…), so there is "
                f"nothing to unwrap — expected credential_offer_uri= or request_uri= [no_retry]"
            )

        params = parse_qs(parts.query)
        for param, scheme in _FLOW_SCHEMES:
            if param in params:
                logger.info(f"[paradym_provider] Unwrapping paradym invitation as {scheme}://")
                return f"{scheme}://?{parts.query}"

        raise ValueError(
            f"Paradym invitation carries none of {', '.join(p for p, _ in _FLOW_SCHEMES)} "
            f"({url[:80]}…), so the flow it belongs to cannot be determined [no_retry]"
        )
