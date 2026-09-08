"""
Pipeline wrappers: acquire a repo, run the analysis core, and (online only)
render + cache a PR-comment summary.

Two entry points, one shared core (`app.analysis.analyze_impacts`):

- clone_and_analyze_pr : online (webhook worker) — resolves a PR via the GitHub
  API, clones with an installation token, and returns a rendered summary string.
- analyze_refs         : offline — runs on (repo, base_sha, head_sha) with no
  webhook and no comment posting, returning structured impacts.
"""
import os
import logging

from app.git_ops import (
    get_pr_info,
    clone_repo,
    checkout_ref,
    prepare_offline_repo,
)
from app.github import get_github_token
from app.analysis import analyze_impacts, AnalysisResult
from app.render import render_pr_comment
from app.cache import cache_get, cache_set

logger = logging.getLogger(__name__)


def clone_and_analyze_pr(repo: str, pr_number: int, workspace: str) -> str:
    """Online path: PR -> rendered summary string (for posting as a PR comment)."""
    pr = get_pr_info(repo, pr_number)
    head_sha = pr["head"]["sha"]

    summary_cache_key = f"summary:{repo}:{head_sha}"
    cached_summary = cache_get(summary_cache_key)
    if cached_summary:
        logger.info("Returning cached summary", extra={"commit": head_sha})
        return cached_summary

    token = get_github_token(repo)
    base_sha = pr["base"]["sha"]
    repo_dir = os.path.join(workspace, "repo")

    # Only clone if we missed the summary cache.
    clone_repo(repo, token, repo_dir)
    checkout_ref(repo_dir, pr["head"]["ref"])

    result = analyze_impacts(
        repo_dir, repo, base_sha, head_sha, explain=True, use_cache=True
    )
    summary = render_pr_comment(result)

    cache_set(summary_cache_key, summary, ttl=3600)
    return summary


def analyze_refs(
    repo: str,
    base_sha: str,
    head_sha: str,
    workspace: str,
    *,
    local_path: str | None = None,
    explain: bool = False,
    use_cache: bool = False,
    rules_file: str | None = None,
) -> AnalysisResult:
    """Offline path: (repo, base_sha, head_sha) -> structured impacts.

    No GitHub App auth, no webhook, no comment posting. Defaults to no LLM
    (graph-only) and no Redis so it runs with zero secrets/services.
    """
    repo_dir = prepare_offline_repo(
        workspace, repo, base_sha, head_sha, local_path=local_path
    )
    return analyze_impacts(
        repo_dir, repo, base_sha, head_sha,
        explain=explain, use_cache=use_cache, rules_file=rules_file,
    )
