import jwt
import time, os

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_PRIVATE_KEY_PATH = os.getenv("GITHUB_PRIVATE_KEY_PATH")

with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
    GITHUB_PRIVATE_KEY = f.read()

def generate_jwt() -> str:
    current_time = int(time.time())
    payload = {
        "iat": current_time - 60,
        "exp": current_time + 600, # Token valid for 10 minutes
        "iss": GITHUB_APP_ID,
    }
    
    token = jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm="RS256")
    return token
