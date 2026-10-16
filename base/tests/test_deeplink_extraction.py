"""How a provider's response becomes the deeplink a wallet is handed.

`extract_deeplink` is shared by the web provider, which GETs a page, and the api provider, which
calls a backend. They must read a response the same way. When each had its own copy of the scan,
nothing kept them in step.

The href unescape is the case that matters most, because it breaks in a direction that doesn't look
like a harness fault. A page that correctly writes `?client_id=x&amp;request_uri=y` in an attribute
used to come out with a second parameter named `amp;request_uri` — Talao's sandbox verifiers do
exactly this. The wallet then rejects a request with no request_uri, and the matrix blames the
verifier.

The api provider's 4xx handling is pinned for a similar reason. A 4xx is the provider rejecting the
configured request, not a flake. EUDIPLO answers a bad claim set with the exact schema violation,
and retrying the unchanged request twice more only buries that message under two identical
failures.
"""
import json
from pathlib import Path

import pytest
import requests

from generate_compact_report import (collection_tallies, group_agents, load_agent_groups,
                                     render_history_by_collection, row_label, table_layout)
from providers.api_provider import ApiDeeplinkProvider
from providers.authbound_provider import AuthboundProvider
from providers.factory import get_provider
from providers.multipaz_provider import MultipazProvider
from providers.web_provider import WebDeeplinkProvider, extract_deeplink

OFFER = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example%2Foffer%2F1"
REQUEST = "openid4vp://?client_id=x509_hash%3Aabc&request_uri=https%3A%2F%2Fverifier.example%2Freq%2F1"


# --- extract_deeplink -----------------------------------------------------------------------------

def test_a_bare_https_body_is_the_deeplink():
    url, _ = extract_deeplink("https://paradym.id/invitation?request_uri=https%3A%2F%2Fx\n")
    assert url == "https://paradym.id/invitation?request_uri=https%3A%2F%2Fx"


def test_a_json_uri_field_is_read():
    url, _ = extract_deeplink('{"uri": "%s", "sessionId": "abc"}' % OFFER)
    assert url == OFFER


def test_a_named_json_field_is_read_instead_of_uri():
    body = '{"authorization_request_uri": "%s", "uri": "https://not-this"}' % REQUEST
    url, _ = extract_deeplink(body, json_field="authorization_request_uri")
    assert url == REQUEST


def test_a_json_field_that_is_not_a_deeplink_is_not_returned_as_one():
    # A plain https value is not an offer, so the key matching is not enough. Handing a wallet a
    # status URL would surface as the wallet rejecting a "bad QR", far from the real cause.
    assert extract_deeplink('{"uri": "https://example.com/status"}') is None


def test_a_deeplink_printed_in_page_source_is_found():
    html = '<html><body><a class="btn">Open</a><p>%s</p></body></html>' % OFFER
    url, _ = extract_deeplink(html)
    assert url == OFFER


def test_an_href_invitation_is_html_unescaped():
    href = "https://app.altme.io/app/download/authorize?client_id=did%3Aweb%3Atalao.co&amp;request_uri=https%3A%2F%2Ftalao.co%2Freq"
    url, _ = extract_deeplink('<a href="%s">Open wallet</a>' % href)
    assert "&amp;" not in url
    assert "&request_uri=" in url


def test_a_raw_ampersand_is_not_mistaken_for_a_legacy_entity():
    # html.unescape would turn "&registration=" into "®istration=": "&reg" is a legacy entity
    # that needs no semicolon. Only complete references like "&amp;" may be decoded.
    href = "https://x.example/authorize?client_id=a&registration=%7B%7D&request_uri=https%3A%2F%2Fr"
    url, _ = extract_deeplink('<a href="%s">go</a>' % href)
    assert url == href


def test_a_json_escaped_url_is_decoded_not_scanned_raw():
    # The link is under a key other than "uri", with its "&" JSON-escaped. Scanning the raw text
    # used to hand a wallet the literal "&".
    body = '{"uri": "https://status.example", "crossDeviceUri": "openid4vp://?a=1\\u0026request_uri=x"}'
    url, _ = extract_deeplink(body)
    assert url == "openid4vp://?a=1&request_uri=x"


def test_a_deeplink_under_any_json_key_is_found_and_https_envelopes_are_skipped():
    # paradym's shape: https invitation envelopes first, the openable deeplink under its own key.
    body = ('{"offerUri": "https://paradym.id/invitation?credential_offer_uri=x", '
            '"offerDeeplinkUri": "%s"}' % OFFER)
    url, _ = extract_deeplink(body)
    assert url == OFFER


def test_a_body_with_no_deeplink_gives_none():
    assert extract_deeplink("<html><body>Nothing to scan here</body></html>") is None


# --- ApiDeeplinkProvider --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Records each call and answers with a canned response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response


def _provider(credential, response):
    provider = ApiDeeplinkProvider("https://api.example/", {"case": credential})
    provider._session = FakeSession(response)
    return provider


def test_a_post_sends_the_configured_body_and_headers_and_reads_the_named_field():
    provider = _provider(
        {"type": "verification", "path": "/api/verify", "body": {"useCase": "sports-shop"},
         "headers": {"X-Demo": "1"}, "response_field": "vpTokenQrCode"},
        FakeResponse(text='{"vpTokenQrCode": "%s"}' % REQUEST),
    )
    assert provider.get("case") == REQUEST
    method, url, kwargs = provider._session.calls[0]
    assert (method, url) == ("POST", "https://api.example/api/verify")
    assert kwargs["json"] == {"useCase": "sports-shop"}
    assert kwargs["headers"] == {"X-Demo": "1"}


def test_a_get_sends_no_body():
    # The config carries a body, so this checks that GET drops it rather than that none was set.
    provider = _provider(
        {"type": "issuance", "path": "offer", "method": "GET", "body": {"credentialId": "x"}},
        FakeResponse(text='{"uri": "%s"}' % OFFER),
    )
    assert provider.get("case") == OFFER
    method, _, kwargs = provider._session.calls[0]
    assert method == "GET"
    assert "json" not in kwargs


def test_a_bare_offer_body_is_accepted():
    # walt.id answers its issue call with the deeplink as the whole body, not JSON.
    provider = _provider({"type": "issuance", "path": "/issue"}, FakeResponse(text=OFFER))
    assert provider.get("case") == OFFER


def test_a_4xx_is_not_retried_and_keeps_the_providers_message():
    provider = _provider(
        {"type": "issuance", "path": "/api/issue", "body": {"credentialId": "pid"}},
        FakeResponse(400, '{"error": "data must have required property \'age_in_years\'"}'),
    )
    with pytest.raises(ValueError) as err:
        provider.get("case")
    assert "[no_retry]" in str(err.value)
    assert "age_in_years" in str(err.value)


def test_a_5xx_stays_retryable():
    provider = _provider({"type": "issuance", "path": "/api/issue"}, FakeResponse(503, "busy"))
    with pytest.raises(requests.HTTPError):
        provider.get("case")


@pytest.mark.parametrize("status", [408, 429])
def test_a_timeout_or_rate_limit_stays_retryable(status):
    provider = _provider({"type": "issuance", "path": "/api/issue"}, FakeResponse(status, "slow down"))
    with pytest.raises(requests.HTTPError):
        provider.get("case")


def test_a_named_response_field_that_is_missing_fails_instead_of_taking_another_link():
    provider = _provider(
        {"type": "verification", "path": "/v", "response_field": "authorization_request_uri"},
        FakeResponse(text='{"uri": "%s"}' % REQUEST),
    )
    with pytest.raises(ValueError) as err:
        provider.get("case")
    assert "authorization_request_uri" in str(err.value)
    assert "[no_retry]" in str(err.value)


def test_a_response_without_a_deeplink_is_not_retried():
    provider = _provider({"type": "issuance", "path": "/api/issue"},
                         FakeResponse(text='{"status": "ok"}'))
    with pytest.raises(ValueError, match=r"\[no_retry\]"):
        provider.get("case")


def test_a_missing_path_is_a_config_error_not_a_flake():
    provider = _provider({"type": "issuance"}, FakeResponse(text=OFFER))
    with pytest.raises(ValueError, match=r"\[no_retry\]"):
        provider.get("case")


def test_the_factory_routes_type_api_to_the_api_provider():
    config = {"test_cases": {"x_issuer": {
        "type": "api", "base_url": "https://api.example",
        "credentials": {"credential_issuance": {"type": "issuance", "path": "/issue"}},
    }}}
    assert isinstance(get_provider(config, "x_issuer"), ApiDeeplinkProvider)


def test_a_dotted_response_field_reads_a_nested_deeplink():
    # iGrant's verifier: the by-value link comes first and must not be the one taken.
    body = '{"by_value": {"uri": "%s&request=unsigned"}, "by_reference": {"uri": "%s"}}' % (REQUEST, REQUEST)
    provider = _provider({"type": "verification", "path": "/presentations",
                          "response_field": "by_reference.uri"}, FakeResponse(text=body))
    assert provider.get("case") == REQUEST


def test_a_dotted_response_field_that_stops_short_is_not_a_deeplink():
    provider = _provider({"type": "verification", "path": "/p", "response_field": "by_reference.uri"},
                         FakeResponse(text='{"by_reference": "%s"}' % REQUEST))
    with pytest.raises(ValueError, match=r"\[no_retry\]"):
        provider.get("case")


def test_a_uuid_placeholder_is_fresh_on_every_call():
    # Hovi's playground refuses a sessionId it saw in the last hour.
    provider = _provider({"type": "issuance", "path": "/issue",
                          "body": {"credentialType": "pid-mdoc", "sessionId": "{{uuid}}"}},
                         FakeResponse(text='{"uri": "%s"}' % OFFER))
    provider.get("case")
    provider.get("case")
    first, second = (kwargs["json"] for _, _, kwargs in provider._session.calls)
    assert first["credentialType"] == "pid-mdoc"
    assert first["sessionId"] != second["sessionId"]
    assert len(first["sessionId"]) == 36


class RoutingSession(FakeSession):
    """A FakeSession whose GETs answer by URL, for bodies that fetch part of themselves."""

    def __init__(self, response, pages):
        super().__init__(response)
        self.pages = pages

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.pages.get(url, self.response)


def test_a_get_placeholder_inlines_the_fetched_text_and_joins_a_path_to_base_url():
    provider = _provider({"type": "issuance", "path": "/issue",
                          "body": {"content": "{{get:/bundle.json}}", "protocol": "P"}},
                         FakeResponse(text='{"url": "%s"}' % OFFER))
    provider._session = RoutingSession(FakeResponse(text='{"url": "%s"}' % OFFER),
                                       {"https://api.example/bundle.json": FakeResponse(text='{"resourceType": "Bundle"}')})
    assert provider.get("case") == OFFER
    method, url, kwargs = provider._session.calls[-1]
    assert (method, url) == ("POST", "https://api.example/issue")
    assert kwargs["json"] == {"content": '{"resourceType": "Bundle"}', "protocol": "P"}


def test_a_get_placeholder_whose_file_is_gone_names_the_placeholder_and_is_not_retried():
    provider = _provider({"type": "issuance", "path": "/issue",
                          "body": {"content": "{{get:bundle.json}}"}}, FakeResponse(text=OFFER))
    provider._session = RoutingSession(FakeResponse(text=OFFER),
                                       {"https://api.example/bundle.json": FakeResponse(404, "Not Found")})
    with pytest.raises(ValueError, match=r"placeholder \{\{get:bundle\.json\}\}.*HTTP 404.*\[no_retry\]"):
        provider.get("case")


def test_a_placeholder_inside_longer_text_is_left_alone():
    provider = _provider({"type": "issuance", "path": "/issue", "body": {"note": "id {{uuid}} here"}},
                         FakeResponse(text=OFFER))
    provider.get("case")
    assert provider._session.calls[0][2]["json"] == {"note": "id {{uuid}} here"}


LISSI_WALLET_LINK = ("https://wallet.lissi.io/lite?client_id=redirect_uri%3Ahttps%3A%2F%2Fv.example%2Fresponse"
                     "&request_uri=https%3A%2F%2Fv.example%2Freq%2F1&request_uri_method=post&state=a~b%20c")


def test_rewrite_scheme_turns_a_web_wallet_link_into_a_deeplink_with_the_same_query():
    # Lissi answers with a link into its own web wallet, which Android would open in a browser.
    provider = _provider({"type": "verification", "method": "GET", "path": "/pid-presentation/x",
                          "response_field": "redirectUrl", "rewrite_scheme": "openid4vp"},
                         FakeResponse(text='{"qrCodeUrl": "data:image/png;base64,AAAA", '
                                           '"redirectUrl": "%s"}' % LISSI_WALLET_LINK))
    assert provider.get("case") == (
        "openid4vp://?client_id=redirect_uri%3Ahttps%3A%2F%2Fv.example%2Fresponse"
        "&request_uri=https%3A%2F%2Fv.example%2Freq%2F1&request_uri_method=post&state=a~b%20c")


@pytest.mark.parametrize("field_value", ['"https://wallet.lissi.io/lite"', "null"])
def test_rewrite_scheme_with_a_link_that_has_no_query_is_not_retried(field_value):
    provider = _provider({"type": "verification", "method": "GET", "path": "/pid-presentation/x",
                          "response_field": "redirectUrl", "rewrite_scheme": "openid4vp"},
                         FakeResponse(text='{"redirectUrl": %s}' % field_value))
    with pytest.raises(ValueError, match=r"holds none.*\[no_retry\]"):
        provider.get("case")


@pytest.mark.parametrize("scheme", ["openid4vp://", "openid-credentail-offer"])
def test_a_rewrite_scheme_no_wallet_opens_fails_at_mint_time(scheme):
    # Otherwise the typo surfaces on the device as a wallet that never opened.
    provider = _provider({"type": "verification", "method": "GET", "path": "/x",
                          "response_field": "redirectUrl", "rewrite_scheme": scheme},
                         FakeResponse(text='{"redirectUrl": "%s"}' % LISSI_WALLET_LINK))
    with pytest.raises(ValueError, match=r"not a deeplink scheme.*\[no_retry\]"):
        provider.get("case")


def test_rewrite_scheme_without_a_response_field_is_a_config_error():
    provider = _provider({"type": "issuance", "path": "/pid-issuance",
                          "rewrite_scheme": "openid-credential-offer"},
                         FakeResponse(text='{"redirectUrl": "%s"}' % LISSI_WALLET_LINK))
    with pytest.raises(ValueError, match=r"needs response_field to name the field.*\[no_retry\]"):
        provider.get("case")


def test_rewrite_scheme_drops_the_fragment():
    provider = _provider({"type": "verification", "method": "GET", "path": "/x",
                          "response_field": "redirectUrl", "rewrite_scheme": "openid4vp"},
                         FakeResponse(text='{"redirectUrl": "https://w.example/lite?a=%2F+b&c=d#frag"}'))
    assert provider.get("case") == "openid4vp://?a=%2F+b&c=d"


@pytest.mark.parametrize("typo", ["{{uiid}}", "{{ uuid }}", "{{UUID}}", "{{get:}}"])
def test_an_unknown_placeholder_is_rejected_not_sent_literally(typo):
    provider = _provider({"type": "issuance", "path": "/issue", "body": {"sessionId": typo}},
                         FakeResponse(text=OFFER))
    with pytest.raises(ValueError, match=r"is not a body placeholder.*\[no_retry\]"):
        provider.get("case")
    assert provider._session.calls == []


def test_placeholders_are_filled_inside_nested_bodies():
    provider = _provider({"type": "issuance", "path": "/issue",
                          "body": {"outer": {"ids": ["{{uuid}}", "fixed"]}}}, FakeResponse(text=OFFER))
    provider.get("case")
    ids = provider._session.calls[0][2]["json"]["outer"]["ids"]
    assert len(ids[0]) == 36 and ids[1] == "fixed"


def test_a_get_placeholder_with_an_absolute_url_is_fetched_as_given():
    provider = _provider({"type": "issuance", "path": "/issue",
                          "body": {"content": "{{get:https://files.example/b.json}}"}}, FakeResponse(text=OFFER))
    provider._session = RoutingSession(FakeResponse(text=OFFER),
                                       {"https://files.example/b.json": FakeResponse(text="BUNDLE")})
    provider.get("case")
    assert provider._session.calls[0][1] == "https://files.example/b.json"
    assert provider._session.calls[-1][2]["json"] == {"content": "BUNDLE"}


# --- MultipazProvider -----------------------------------------------------------------------------

MULTIPAZ_PAGE = """<form action="authorize" method="post">
<input type="hidden" name="authorizationCode" value="CODE123"/>
<select id="person" name="person"><option value="">[Not authenticated]</option></select></form>
<script>
    let scope = "mDL";
    let personMap = {"p-lee": {"name": "Lee Tom", "instances": {}},
                     "p-jay": {"name": "Jay Peg", "instances": {"i-jay": "Driving licence"}}};
</script>"""


class MultipazSession:
    """Answers the start GET with the identity page and the POST with the finished page."""

    def __init__(self, start, done):
        self.start, self.done = start, done
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.start

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.done


def _multipaz(start_text, done_text=""):
    start = FakeResponse(text=start_text)
    start.url = "https://issuer.multipaz.org/records/authorize?clientId=x&request_uri=y"
    done = FakeResponse(text=done_text)
    done.url = "https://issuer.multipaz.org/issuer/finish_authorization?code=z"
    provider = MultipazProvider("https://issuer.multipaz.org/",
                                {"case": {"type": "issuance", "configuration_id": "mDL"}})
    provider._session = MultipazSession(start, done)
    return provider


def test_multipaz_posts_the_page_code_for_a_person_who_holds_the_record():
    # Lee Tom is listed first but has no driving licence; an offer for him would have nothing to issue.
    provider = _multipaz(MULTIPAZ_PAGE, '<a href="%s">Open in wallet</a>' % OFFER)
    assert provider.get("case") == OFFER
    (_, start_url, start_kwargs), (_, post_url, post_kwargs) = provider._session.calls
    assert start_url == "https://issuer.multipaz.org/issuer/authorize"
    assert start_kwargs["params"] == {"configuration_id": "mDL", "url_schema": "openid-credential-offer",
                                      "request_uri": "https://pre-authorize.multipaz.org/", "tx_kind": "none"}
    assert post_url == "https://issuer.multipaz.org/records/authorize"
    assert post_kwargs["data"] == {"authorizationCode": "CODE123", "person": "p-jay", "instance": "i-jay"}


def test_multipaz_takes_the_first_person_for_a_record_everyone_has():
    assert MultipazProvider._choose({"p-a": {"instances": {}}, "p-b": {"instances": {}}}, "core") == ("p-a", "")


def test_multipaz_refuses_when_nobody_holds_the_requested_record():
    # An offer for a person without an mDL resolves and then has nothing to issue.
    page = MULTIPAZ_PAGE.replace('{"i-jay": "Driving licence"}', '{}')
    with pytest.raises(ValueError, match=r"holds a 'mDL' record.*\[no_retry\]"):
        _multipaz(page).get("case")


def test_multipaz_with_nobody_listed_is_not_retried():
    page = MULTIPAZ_PAGE.split("let personMap")[0] + "let personMap = {};</script>"
    with pytest.raises(ValueError, match=r"lists nobody.*\[no_retry\]"):
        _multipaz(page).get("case")


def test_multipaz_start_page_rejected_is_not_retried():
    provider = _multipaz(MULTIPAZ_PAGE)
    provider._session.start = FakeResponse(404, "no such configuration")
    with pytest.raises(ValueError, match=r"GET .*issuer/authorize.*HTTP 404.*\[no_retry\]"):
        provider.get("case")


def test_multipaz_says_so_when_the_identity_page_has_changed():
    with pytest.raises(ValueError, match=r"flow has changed.*\[no_retry\]"):
        _multipaz("<html>Something else entirely</html>").get("case")


def test_multipaz_without_an_offer_on_the_last_page_is_not_retried():
    with pytest.raises(ValueError, match=r"\[no_retry\]"):
        _multipaz(MULTIPAZ_PAGE, "<html>Not authenticated</html>").get("case")


def test_multipaz_rejecting_the_person_is_not_retried():
    provider = _multipaz(MULTIPAZ_PAGE)
    provider._session.done = FakeResponse(400, "unknown person")
    with pytest.raises(ValueError, match=r"POST .*records/authorize.*HTTP 400.*\[no_retry\]"):
        provider.get("case")


def test_multipaz_with_an_unreadable_person_list_says_the_flow_has_changed():
    page = MULTIPAZ_PAGE.replace('"instances": {}}', '"instances": {}')  # unbalanced JSON
    with pytest.raises(ValueError, match=r"flow has changed.*\[no_retry\]"):
        _multipaz(page).get("case")


def test_the_factory_routes_type_multipaz_to_the_multipaz_provider():
    config = {"test_cases": {"m_issuer": {
        "type": "multipaz", "base_url": "https://issuer.multipaz.org",
        "credentials": {"credential_issuance": {"type": "issuance", "configuration_id": "pid_mdoc"}},
    }}}
    assert isinstance(get_provider(config, "m_issuer"), MultipazProvider)


# --- the registry ---------------------------------------------------------------------------------
# Checks the committed config/providers.json itself. A credential-typed entry carries what the
# reporting stage reads, so nobody re-derives it from a live issuer that may have moved on.

_REGISTRY = json.loads((Path(__file__).resolve().parents[2] / "config" / "providers.json").read_text())


def _entries():
    for section, suffix in (("issuers", "issuer"), ("verifiers", "verifier")):
        for name, entry in _REGISTRY[section].items():
            yield section, f"{name}_{suffix}", entry


_PROVIDER_CLASSES = {"api": ApiDeeplinkProvider, "multipaz": MultipazProvider,
                     "authbound": AuthboundProvider, None: WebDeeplinkProvider}


@pytest.mark.parametrize("section,row,entry", list(_entries()), ids=[row for _, row, _ in _entries()])
def test_every_registry_entry_gets_the_provider_its_type_names(section, row, entry):
    # get_provider falls back to the web provider for a type it doesn't know, so a typo such as
    # "multpaz" would still build *something*; only the class says whether the type was read.
    if entry.get("type") == "itb":
        pytest.skip("needs ITB credentials to construct")
    assert type(get_provider({"test_cases": {row: entry}}, row)) is _PROVIDER_CLASSES[entry.get("type")]


# Their request names the credential in the vendor's own terms (Hovi's playground type, the IPS
# demo's protocol) or not at all (Lissi's issuance call takes no body; its id "pid" only appears
# in the minted offer), so the recorded configuration id cannot be checked in it.
_ID_NOT_IN_REQUEST = {"hovi_playground_pid_mdoc", "hovi_playground_pid_sdjwt", "findynet_ips",
                      "lissi_pid_sdjwt"}


@pytest.mark.parametrize("name,entry",
                         [(n, e) for n, e in _REGISTRY["issuers"].items()
                          if "credential_configuration_id" in e and n not in _ID_NOT_IN_REQUEST])
def test_the_recorded_configuration_id_is_the_one_the_request_asks_for(name, entry):
    # The report describes the configuration named at the top level; an offer for another one
    # would make that description silently wrong.
    assert entry["credential_configuration_id"] in set(_leaves(entry["credentials"]))


def _leaves(value):
    """Every string in a config structure, so an id must be a whole value, not part of a path."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)
    elif isinstance(value, str):
        yield value


@pytest.mark.parametrize("section,row,entry",
                         [e for e in _entries() if "credential_type" in e[2]],
                         ids=[row for _, row, entry in _entries() if "credential_type" in entry])
def test_a_credential_typed_entry_carries_its_reporting_identity(section, row, entry):
    ctype = entry["credential_type"]
    assert ctype["name"] and ctype["format"] in ("mso_mdoc", "dc+sd-jwt", "vc+sd-jwt")
    assert ctype.get("doctype" if ctype["format"] == "mso_mdoc" else "vct")
    if section == "issuers":
        assert entry["issuer_metadata"].startswith("https://") and "/.well-known/openid-credential-issuer" in entry["issuer_metadata"]
        assert entry["credential_configuration_id"]


# --- report grouping ------------------------------------------------------------------------------
# Lives here rather than in its own file because it is the other half of the same change: providers
# now carry a "group", and the report sorts rows by it.

def test_groups_are_read_from_the_registry(tmp_path):
    registry = tmp_path / "providers.json"
    registry.write_text('{"issuers": {"hovi": {"group": "Pension demo"}, "plain": {}},'
                        ' "verifiers": {"hovi": {"group": "Pension demo"}}}')
    assert load_agent_groups(registry) == {"hovi_issuer": "Pension demo",
                                           "hovi_verifier": "Pension demo"}


def test_a_missing_registry_gives_no_groups(tmp_path):
    assert load_agent_groups(tmp_path / "missing.json") == {}


def test_rows_split_into_groups_in_registry_order():
    groups = {"hovi_issuer": "Pension demo", "talao_pid_issuer": "FIDES Community",
              "waltid_issuer": "Pension demo"}
    rows = ["hovi_issuer", "talao_pid_issuer", "waltid_issuer"]
    assert group_agents(rows, groups) == [
        ("Pension demo", ["hovi_issuer", "waltid_issuer"]),
        ("FIDES Community", ["talao_pid_issuer"]),
    ]


def test_an_ungrouped_registry_renders_as_one_unlabelled_block():
    assert group_agents(["a_issuer", "b_issuer"], {}) == [(None, ["a_issuer", "b_issuer"])]


def test_a_row_missing_from_the_registry_is_kept_under_other():
    # An older run rendered today can have a provider that has since been removed.
    assert group_agents(["hovi_issuer", "gone_issuer"], {"hovi_issuer": "Pension demo"}) == [
        ("Pension demo", ["hovi_issuer"]), ("Other", ["gone_issuer"]),
    ]


def test_the_group_is_the_section_and_the_flow_sits_under_it():
    groups = {"hovi_issuer": "Pension demo", "hovi_verifier": "Pension demo",
              "talao_pid_issuer": "FIDES Community"}
    layout = table_layout(["hovi_issuer", "talao_pid_issuer"], ["hovi_verifier"], groups)
    assert layout == [
        ("Pension demo", [("Issuers", ["hovi_issuer"], "issuance"),
                          ("Verifiers", ["hovi_verifier"], "verification")]),
        ("FIDES Community", [("Issuers", ["talao_pid_issuer"], "issuance")]),
    ]


def test_without_groups_the_flow_is_the_section():
    assert table_layout(["a_issuer"], ["a_verifier"], {}) == [
        ("Issuers", [(None, ["a_issuer"], "issuance")]),
        ("Verifiers", [(None, ["a_verifier"], "verification")]),
    ]


def test_a_flow_with_no_rows_gets_no_heading():
    # An issuance-only run used to render an empty "Other" section holding a bare "Verifiers".
    assert table_layout(["hovi_issuer"], [], {"hovi_issuer": "Pension demo"}) == [
        ("Pension demo", [("Issuers", ["hovi_issuer"], "issuance")]),
    ]
    assert table_layout(["a_issuer"], [], {}) == [("Issuers", [(None, ["a_issuer"], "issuance")])]


def test_a_row_drops_its_role_because_the_heading_already_says_it():
    assert row_label("authbound_issuer") == "Authbound"
    assert row_label("eudiplo_loyalty_verifier") == "EUDIPLO Loyalty"
    assert row_label("talao_pid_issuer") == "Talao PID"


_GROUPS = {"hovi_issuer": "Pension demo", "hovi_verifier": "Pension demo",
           "talao_pid_issuer": "FIDES Community"}


def test_history_is_counted_per_collection_as_passed_out_of_all():
    entry = {"cells": {
        "issuance": {"heidi": {"hovi_issuer": "Passed", "talao_pid_issuer": "Failed:rejected"},
                     "paradym": {"hovi_issuer": "Error:absent", "talao_pid_issuer": "Passed"}},
        "verification": {"heidi": {"hovi_verifier": "Passed"}},
    }}
    assert collection_tallies(entry, _GROUPS) == {"Pension demo": (2, 3), "FIDES Community": (1, 2)}


def test_a_collection_missing_from_a_run_is_absent_not_zero():
    # An older run from before the FIDES providers existed: "not tested" must not read as "0/0".
    entry = {"cells": {"issuance": {"heidi": {"hovi_issuer": "Passed"}}}}
    assert collection_tallies(entry, _GROUPS) == {"Pension demo": (1, 1)}


def test_skipped_cells_are_not_counted_and_unknown_providers_go_under_other():
    entry = {"cells": {"issuance": {"heidi": {"hovi_issuer": "Skipped", "gone_issuer": "Passed"}}}}
    assert collection_tallies(entry, _GROUPS) == {"Other": (1, 1)}


def _run(ts, cells, passed):
    return {"run_ts": ts, "totals": {"Passed": passed}, "cells": {"issuance": {"heidi": cells}},
            "wallets": {}, "no_report": []}


def test_the_collection_history_shows_a_dash_where_a_collection_was_not_run():
    history = [_run("2026-09-01_22-00-00", {"hovi_issuer": "Passed"}, 1),
               _run("2026-09-02_22-00-00", {"hovi_issuer": "Passed", "talao_pid_issuer": "Passed"}, 2)]
    out = render_history_by_collection(history, "2026-09-02_22-00-00", _GROUPS)
    assert 'title="1 of 1 FIDES Community tests passed"' in out
    # The first run predates the FIDES providers: a dash, not "0/0".
    assert "<td class='coll none' title='Not part of this run'>&mdash;</td>" in out


def test_the_collection_history_is_omitted_without_groups():
    history = [_run("2026-09-01_22-00-00", {"a_issuer": "Passed"}, 1),
               _run("2026-09-02_22-00-00", {"a_issuer": "Passed"}, 1)]
    assert render_history_by_collection(history, "2026-09-02_22-00-00", {}) == ""
