import requests
import os

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_APP_TOKEN")

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

    if response.status_code >= 300:
        raise RuntimeError(
            f"GitHub API error {response.status_code}: {response.text}"
        )
