class DeeplinkProvider:
    """Abstract source of credential offer deeplinks.

    Implementations:
      - ConfigDeeplinkProvider  — reads static URLs from config JSON
      - WebDeeplinkProvider     — fetches a provider webpage and extracts the URL
      - future: QrDeeplinkProvider — scans a physical QR code via device camera
    """

    def get(self, name: str) -> str:
        raise NotImplementedError
