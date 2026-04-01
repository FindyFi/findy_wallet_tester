import os
import sys
import pytest
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path so `base` and `wallets` are importable.
sys.path.insert(0, str(Path(__file__).parent.parent))

from base.utils import list_wallets, TIMESTAMP_FORMAT

wallets_dir = Path(__file__).parent.parent / "wallets"


def main():
    # If wallet names are passed as arguments, use those; otherwise run all wallets.
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    _extra_pytest_args = [a for a in sys.argv[1:] if a.startswith("-")]

    if _args:
        unknown = [w for w in _args if w not in list_wallets()]
        if unknown:
            print(f"Unknown wallet(s): {', '.join(unknown)}")
            print(f"Available: {', '.join(list_wallets())}")
            sys.exit(1)
        wallets_to_test = _args
    else:
        wallets_to_test = list_wallets()

    # Create one shared session directory; each wallet gets its own subdirectory inside.
    timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
    session_dir = Path("reports") / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_SESSION_DIR"] = str(session_dir)

    exit_codes = []
    for wallet in wallets_to_test:
        code = pytest.main([str(wallets_dir / wallet / "tests"), "-v"] + _extra_pytest_args)
        exit_codes.append(int(code))

    sys.exit(max(exit_codes) if exit_codes else 0)


if __name__ == "__main__":
    main()
