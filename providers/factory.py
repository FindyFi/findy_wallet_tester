import os

from providers.config_provider import ConfigDeeplinkProvider
from providers.web_provider import WebDeeplinkProvider
from providers.base import DeeplinkProvider

_itb_provider_cache: dict = {}


def get_provider(config: dict, issuer_name: str) -> DeeplinkProvider:
    """Return the appropriate DeeplinkProvider for an issuer.

    Looks up the issuer under config["test_cases"][issuer_name] and selects:
      - ItbProvider            when ``type`` is ``"itb"``
        (authenticates to itb.ilabs.ai and drives a test session)
      - WebDeeplinkProvider    when the issuer has a ``base_url``
        (credentials specify a ``path`` suffix)
      - ConfigDeeplinkProvider when there is no ``base_url``
        (credentials specify a static ``deeplink`` URL)

    Raises ValueError if the issuer is not found.
    """
    issuer = config.get("test_cases", {}).get(issuer_name)
    if not issuer:
        raise ValueError(
            f"No issuer '{issuer_name}' found under 'test_cases' in config"
        )

    credentials = issuer.get("credentials", {})

    if issuer.get("type") == "itb":
        from providers.itb_provider import ItbProvider
        username = os.path.expandvars(issuer.get("username", ""))
        password = os.path.expandvars(issuer.get("password", ""))
        key = (issuer["base_url"], username, password)
        if key not in _itb_provider_cache:
            _itb_provider_cache[key] = ItbProvider(
                base_url=issuer["base_url"],
                system_id=issuer["system_id"],
                credentials=credentials,
                username=username,
                password=password,
            )
        return _itb_provider_cache[key]

    base_url = issuer.get("base_url")
    if base_url:
        return WebDeeplinkProvider(base_url, credentials)
    return ConfigDeeplinkProvider(credentials)
