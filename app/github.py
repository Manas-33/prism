import requests
import os
import logging
from app.github_auth import generate_jwt

GITHUB_API = "https://api.github.com"
logger = logging.getLogger(__name__)

def post_pr_comment(repo: str, pr_number: int, body: str):
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    token = get_github_token(repo)
    headers = {
        "Authorization": f"Bearer {token}",
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

def get_installation_id(jwt_token: str, repo: str) -> int:
    owner , repo_name = repo.split("/")
    url = f"{GITHUB_API}/repos/{owner}/{repo_name}/installation"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        logger.error(
            "Failed to get installation ID",
            extra={"status": response.status_code, "url": url},
        )
        raise RuntimeError(
            f"Failed to get installation ID: {response.status_code} {response.text}"
        )
    response.raise_for_status()
    installation_data = response.json()
    installation_id = installation_data["id"]
    return installation_id

def get_installation_token(jwt_token: str, installation_id: int) -> str:
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }
    response = requests.post(url, headers=headers)
    if response.status_code != 201:
        logger.error(
            "Failed to get installation token",
            extra={"status": response.status_code, "url": url},
        )
        raise RuntimeError(
            f"Failed to get installation token: {response.status_code} {response.text}"
        )
    response.raise_for_status()
    token_data = response.json()
    installation_token = token_data["token"]
    return installation_token

def get_github_token(repo:str) -> str:
    jwt_token = generate_jwt()
    installation_id = get_installation_id(jwt_token, repo)
    installation_token = get_installation_token(jwt_token, installation_id)
    return installation_token