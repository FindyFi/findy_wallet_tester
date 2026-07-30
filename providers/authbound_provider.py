import logging

import requests

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


class AuthboundProvider(DeeplinkProvider):
    """Mints credential offers / presentation requests from the Authbound demo backend.

    Unlike WebDeeplinkProvider (which GETs a static page and scrapes a deeplink), Authbound
    (https://authbound.pensiondemo.findy.fi) generates each offer/request dynamically via a POST
    and returns it as an explicit JSON field — so there is nothing to scrape.

    Instantiated per issuer via factory.get_provider(config, issuer_name) when ``type`` is
    ``"authbound"``.

    Config format (test_cases section):
        "authbound_issuer": {
            "type": "authbound",
            "base_url": "https://authbound.pensiondemo.findy.fi",
            "credentials": {
                "credential_issuance": { "type": "issuance", "slug": "kael" }
            }
        },
        "authbound_verifier": {
            "type": "authbound",
            "base_url": "https://authbound.pensiondemo.findy.fi",
            "credentials": {
                "pension_verification": { "type": "verification" }
            }
        }

    Endpoints:
      - issuance:     POST /offer  {"slug": "<slug>"}  -> {"offer": {"id", "offerUri"}, ...}
      - verification: POST /verify (no body)           -> {"verification": {"id", "clientAction":
                                                            {"data"}, "verificationUrl"}}
    """

    def __init__(self, base_url: str, credentials: dict):
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._session = requests.Session()

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        cred_type = cred.get("type")
        if cred_type == "issuance":
            return self._get_offer(name, cred)
        if cred_type == "verification":
            return self._get_verification(name, cred)
        raise ValueError(
            f"Credential '{name}' has unsupported type {cred_type!r} for authbound provider — "
            f"expected 'issuance' or 'verification' [no_retry]"
        )

    def _get_offer(self, name: str, cred: dict) -> str:
        slug = cred.get("slug")
        if not slug:
            raise ValueError(
                f"No 'slug' configured for credential '{name}' — "
                f"add 'slug' under credentials.{name} in the authbound issuer config [no_retry]"
            )

        offer_url = f"{self._base_url}/offer"
        logger.info(f"[authbound_provider] POST {offer_url} (slug={slug})")
        resp = self._session.post(offer_url, json={"slug": slug}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        offer_uri = (data.get("offer") or {}).get("offerUri") or data.get("offerUri")
        if not offer_uri:
            raise ValueError(
                f"Offer response from {offer_url} did not include an offerUri [no_retry]"
            )
        return offer_uri

    def _get_verification(self, name: str, cred: dict) -> str:
        verify_url = f"{self._base_url}/verify"
        logger.info(f"[authbound_provider] POST {verify_url}")
        resp = self._session.post(verify_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        verification = data.get("verification") or data
        request_url = (
            (verification.get("clientAction") or {}).get("data")
            or verification.get("verificationUrl")
            or data.get("authorizationRequestUrl")
        )
        if not request_url:
            raise ValueError(
                f"Verification response from {verify_url} did not include a request URL [no_retry]"
            )
        return request_url
