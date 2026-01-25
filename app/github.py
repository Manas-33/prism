import requests
import os
import logging

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_APP_TOKEN")
logger = logging.getLogger(__name__)

def post_pr_comment(repo: str, pr_number: int, body: str):
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    response = requests.post(
        url,
        headers=headers,
        json={"body": body},
    )
    logger.info("Posted PR comment", extra={"repo": repo, "pr": pr_number})

    if response.status_code >= 300:
        logger.error(
            "GitHub API error",
            extra={"status": response.status_code, "url": url},
        )
        raise RuntimeError(
            f"GitHub API error {response.status_code}: {response.text}"
        )
