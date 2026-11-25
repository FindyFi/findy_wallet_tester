import logging
import re
import uuid

from urllib.parse import urlsplit

import requests

from providers.base import DeeplinkProvider
from providers.web_provider import _OFFER_SCHEMES, _json_field, _scan_json_uri, extract_deeplink

logger = logging.getLogger(__name__)

# A body value that is exactly "{{uuid}}" or "{{get:<url>}}". Whole values only, like the
# `${VAR:-default}` form in base/config.py, so a literal "{{" inside longer text is left alone.
# Braces rather than `${…}` because the config loader expands `${…}` from the environment first.
_PLACEHOLDER = re.compile(r"^\{\{(uuid|get:(.+))\}\}$")


def raise_for_rejection(resp, what: str) -> None:
    """Raise for a failed response, tagging a 4xx `[no_retry]`.

    A 4xx is the provider rejecting the configured request, not a flake — its body says which
    field it disliked (EUDIPLO answers a bad claim set with the exact schema violation), and
    retrying an unchanged request twice more only buries that message. 408 and 429 are the
    exceptions: a timeout or a rate limit on a public playground is exactly the transient failure
    a rerun exists for, so they stay retryable, as does any 5xx.
    """
    if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
        raise ValueError(
            f"{what} was rejected with HTTP {resp.status_code}: {resp.text[:300]} [no_retry]"
        )
    resp.raise_for_status()


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
        response_field  JSON field holding the deeplink; dotted for a nested one (see below)
        rewrite_scheme  e.g. "openid4vp": return `<scheme>://?<query>` built from the https link
                        in response_field. Lissi answers with a link into its own web wallet
                        (`https://wallet.lissi.io/lite?request_uri=…`), which Android hands to
                        a browser, never to the wallet under test; its query is the request.

    Two body values are filled in per call rather than sent as written:
        "{{uuid}}"        a fresh UUID. Hovi's playground refuses a sessionId it has seen in
                          the last hour, so a fixed one in config fails every run but the first.
        "{{get:<url>}}"   the text of a GET to <url> (a path is joined to base_url). The Findynet
                          IPS demo posts a 50 kB patient-summary bundle that its own page fetches
                          from beside it; fetching the same file keeps it out of the registry and
                          in step with what the demo itself sends.

    Without `response_field` the response is read with the same `extract_deeplink` the web
    provider uses, so an API that answers with a bare URL (walt.id) and one that answers with JSON
    (EUDIPLO) both work without saying which is which in config.

    With `response_field` set (dotted for a nested field, e.g. iGrant's `by_reference.uri`), that
    field must hold the deeplink or the call fails `[no_retry]`.
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
        headers = cred.get("headers") or {}
        field = cred.get("response_field")

        logger.info(f"[api_provider] {method} {url}")
        if method == "GET":
            resp = self._session.get(url, headers=headers, timeout=30)
        else:
            body = self._fill(cred.get("body"))
            resp = self._session.request(method, url, json=body, headers=headers, timeout=30)

        raise_for_rejection(resp, f"{method} {url}")

        scheme = cred.get("rewrite_scheme")
        if scheme:
            return self._rewritten(resp.text, field, scheme, url)

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

    @staticmethod
    def _rewritten(text: str, field, scheme: str, url: str) -> str:
        """`<scheme>://?<query>` from the https link in `field`, keeping its query as it is."""
        if f"{scheme}://" not in _OFFER_SCHEMES:
            raise ValueError(
                f"rewrite_scheme '{scheme}' is not a deeplink scheme a wallet opens — write it "
                f"without '://', one of: {', '.join(s[:-3] for s in _OFFER_SCHEMES)} [no_retry]"
            )
        if not field:
            raise ValueError(
                f"rewrite_scheme needs response_field to name the field holding the link [no_retry]"
            )
        link = _json_field(text, field)
        query = urlsplit(link).query if isinstance(link, str) else ""
        if not query:
            raise ValueError(
                f"rewrite_scheme needs response_field to name a link with a query; the "
                f"'{field}' field of the response from {url} holds none, got: {text[:200]} [no_retry]"
            )
        return f"{scheme}://?{query}"

    def _fill(self, value):
        """The configured body with its "{{…}}" placeholders replaced for this call."""
        if isinstance(value, dict):
            return {k: self._fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._fill(v) for v in value]
        if not isinstance(value, str):
            return value
        match = _PLACEHOLDER.match(value)
        if not match:
            # A value shaped like a placeholder but not one of the two is a typo ("{{uiid}}"), and
            # sending it literally fails somewhere far from the config that caused it.
            if value.startswith("{{") and value.endswith("}}"):
                raise ValueError(
                    f"{value} is not a body placeholder; use {{{{uuid}}}} or {{{{get:<url>}}}} [no_retry]"
                )
            return value
        if match.group(1) == "uuid":
            return str(uuid.uuid4())
        source = match.group(2)
        if not source.startswith(("http://", "https://")):
            source = f"{self._base_url}/{source.lstrip('/')}"
        resp = self._session.get(source, timeout=30)
        raise_for_rejection(resp, f"GET {source} for the body placeholder {value}")
        return resp.text
