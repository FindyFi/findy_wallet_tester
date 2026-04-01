from providers.base import DeeplinkProvider


class ConfigDeeplinkProvider(DeeplinkProvider):
    """Reads credential offer deeplinks directly from the issuer's credentials config.

    Instantiated per issuer via factory.get_provider(config, issuer_name).

    Config format (test_cases section):
        "test_cases": {
            "my_issuer": {
                "credentials": {
                    "pid_issuance":  { "type": "issuance",     "deeplink": "openid-credential-offer://..." },
                    "pid_verif":     { "type": "verification",  "deeplink": "openid4vp://..." }
                }
            }
        }

    Use this when you already have a known, static offer URL.
    For dynamic URLs fetched from an issuer website, use WebDeeplinkProvider.
    """

    def __init__(self, credentials: dict):
        self._credentials = credentials

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        url = cred.get("deeplink")
        if not url:
            raise ValueError(
                f"No deeplink configured for credential '{name}' — "
                f"add 'deeplink' under credentials.{name} in the issuer config"
            )
        return url
