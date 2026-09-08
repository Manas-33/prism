import os, subprocess
import requests
from app.github import get_github_token

import logging
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# GitHub PR metadata (online path only)
# ---------------------------------------------------------------------------
def get_pr_info(repo: str, pr_number: int):
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    response = requests.get(url, headers={
            "Authorization": f"Bearer {get_github_token(repo)}",
            "Accept": "application/vnd.github+json",
        }
    )
    response.raise_for_status()
    pr_data = response.json()
    return pr_data


# ---------------------------------------------------------------------------
# Low-level git plumbing (shared by the online and offline paths)
# ---------------------------------------------------------------------------
def clone_repo(repo: str, token: str, dest: str):
    """Authenticated shallow clone for the GitHub App (webhook) path."""
    repo_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(["git", "clone", "--depth", "50", repo_url, dest], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clone_source(source: str, dest: str):
    """Unauthenticated clone from a local path or a public https URL (offline path)."""
    subprocess.run(["git", "clone", source, dest], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def checkout_ref(repo_dir: str, ref: str):
    """Fetch and check out a named ref (e.g. a PR head branch)."""
    subprocess.run(["git", "fetch", "origin", ref], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=repo_dir, check=True)


def ensure_sha(repo_dir: str, sha: str):
    """Make sure a commit SHA is present locally, fetching it by SHA if needed."""
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=repo_dir, capture_output=True,
    ).returncode == 0
    if present:
        return
    subprocess.run(["git", "fetch", "--depth", "50", "origin", sha], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def checkout_sha(repo_dir: str, sha: str):
    subprocess.run(["git", "checkout", "--detach", sha], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def compute_diff(repo_dir: str, base_sha: str) -> str:
    """Raw unified diff of the working tree (HEAD checkout) against base_sha."""
    result = subprocess.run(["git", "diff", base_sha], cwd=repo_dir, check=True, capture_output=True, text=True)
    return result.stdout


def prepare_offline_repo(workspace: str, repo: str, base_sha: str, head_sha: str, *, local_path: str | None = None) -> str:
    """
    Materialize a repo on disk for offline analysis, with HEAD at head_sha and
    base_sha reachable in history — no GitHub App auth, no webhook.

    source: a local git repo path (--local) or, by default, the public
    https://github.com/{repo}.git URL.
    """
    repo_dir = os.path.join(workspace, "repo")
    source = local_path if local_path else f"https://github.com/{repo}.git"
    clone_source(source, repo_dir)
    ensure_sha(repo_dir, base_sha)
    ensure_sha(repo_dir, head_sha)
    checkout_sha(repo_dir, head_sha)
    return repo_dir
