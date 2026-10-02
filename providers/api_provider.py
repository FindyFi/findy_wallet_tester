import logging

import requests

from providers.base import DeeplinkProvider
from providers.web_provider import _scan_json_uri, extract_deeplink

logger = logging.getLogger(__name__)


class ApiDeeplinkProvider(DeeplinkProvider):
    """Mints an offer or presentation request by calling a provider's own HTTP API.

    The demo issuers this suite started with serve a page whose body *is* the deeplink, so
    `WebDeeplinkProvider` only has to GET a URL. Almost every public issuer and verifier in the
    FIDES catalogs is instead a single-page app that asks its backend for a fresh offer, and
    scraping the rendered page is hopeless — the HTML that arrives over HTTP contains no deeplink
    at all, only the JavaScript that will later fetch one.

    Those backends are, however, plain JSON APIs, and they differ only in URL, request body,
    headers, and which field of the answer carries the URL. So this provider is driven entirely
    from `config/providers.json` rather than being a class per vendor: adding an issuer that works
    this way is a registry entry, not Python. `authbound_provider` predates it and stays as it is:
    it reads two different response shapes for its two directions, which this cannot express.

    Config format (config/providers.json):
        "eudiplo": {
            "type": "api",
            "base_url": "https://playground.eudi-wallet.org",
            "credentials": {
                "credential_issuance": {
                    "type": "issuance",
                    "path": "/api/issue",
                    "body": { "credentialId": "pid", "claims": { ... } }
                }
            }
        }

    Per-credential keys:
        path            required — appended to base_url
        method          "POST" (default) or "GET"
        body            JSON request body; omitted entirely for a GET
        headers         extra request headers, e.g. walt.id's authorizeBaseUrl
        response_field  top-level JSON field holding the deeplink (see below)

    Without `response_field` the response is read with the same `extract_deeplink` the web
    provider uses, so an API that answers with a bare URL (walt.id) and one that answers with JSON
    (EUDIPLO) both work without saying which is which in config.

    With `response_field` set, that field must hold the deeplink or the call fails `[no_retry]`.
    Naming a field is a claim about the response, and a misnamed one must not be rescued by
    whichever other link the scan happens to find: some responses carry two (EUDIPLO's
    same-device and cross-device URIs), and silently taking the wrong one surfaces far away, as a
    wallet failure.
    """

    def __init__(self, base_url: str, credentials: dict):
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._session = requests.Session()

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        path = cred.get("path")
        if not path:
            raise ValueError(
                f"No path configured for credential '{name}' — "
                f"add 'path' under credentials.{name} in the api provider config [no_retry]"
            )

        url = f"{self._base_url}/{path.lstrip('/')}"
        method = str(cred.get("method", "POST")).upper()
        body = cred.get("body")
        headers = cred.get("headers") or {}
        field = cred.get("response_field")

        logger.info(f"[api_provider] {method} {url}")
        if method == "GET":
            resp = self._session.get(url, headers=headers, timeout=30)
        else:
            resp = self._session.request(method, url, json=body, headers=headers, timeout=30)

        # A 4xx here is the provider rejecting the configured request, not a flake — its body
        # says which field it disliked (EUDIPLO answers a bad claim set with the exact schema
        # violation), and retrying an unchanged request twice more only buries that message.
        # 408 and 429 are the exceptions: a timeout or a rate limit on a public playground is
        # exactly the transient failure a rerun exists for, so they stay retryable below.
        if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
            raise ValueError(
                f"{method} {url} was rejected with HTTP {resp.status_code}: "
                f"{resp.text[:300]} [no_retry]"
            )
        resp.raise_for_status()

        if field:
            deeplink = _scan_json_uri(resp.text, field)
            if deeplink:
                logger.info(f"[api_provider] Found deeplink in JSON '{field}' field")
                return deeplink
            raise ValueError(
                f"The response from {url} has no deeplink in its '{field}' field — check "
                f"response_field in config, got: {resp.text[:200]} [no_retry]"
            )

        found = extract_deeplink(resp.text)
        if found:
            deeplink, how = found
            logger.info(f"[api_provider] {how}")
            return deeplink

        raise ValueError(
            f"No deeplink in the response from {url} — expected a bare offer URL or a JSON "
            f"body holding one, got: {resp.text[:200]} [no_retry]"
        )
