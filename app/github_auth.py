import jwt
import time, os
from functools import lru_cache

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH")


@lru_cache(maxsize=1)
def _load_private_key() -> str:
    # Loaded lazily (not at import time) so the analysis core can be imported
    # without GitHub App secrets — e.g. for offline invocation, evals, and tests.
    if not GITHUB_PRIVATE_KEY_PATH:
        raise RuntimeError("GITHUB_PRIVATE_KEY_PATH is not set")
    with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
        return f.read()


def generate_jwt() -> str:
    current_time = int(time.time())
    payload = {
        "iat": current_time - 60,
        "exp": current_time + 600, # Token valid for 10 minutes
        "iss": GITHUB_APP_ID,
    }

    token = jwt.encode(payload, _load_private_key(), algorithm="RS256")
    return token
