from base.utils import wait_present

# Hovi wallet uses a secret key (no PIN/password lock screen).
# This stub exists for interface compatibility with the init_flow template.
HEADING = None


def on_screen(driver, timeout: float = 2) -> bool:
    """Hovi has no lock screen — always returns False."""
    return False
