import requests
import os, subprocess
from app.github import get_github_token
from app.static_analysis import (
    changed_files_from_diff,
    changed_lines_from_diff,
    parse_code,
    extract_symbols,
    extract_imports,
    find_changed_symbols,
)
from app.dependency_graph import (
    build_file_graph,
    build_symbol_graph,
    find_impacts_with_confidence_and_context,
)
from app.llm_service import explain_impact
from app.repo_index import build_repo_index
from app.cache import cache_get, cache_set
from app.models import serialize_repo_index, deserialize_repo_index, serialize_symbol_graph, deserialize_symbol_graph
from concurrent.futures import ThreadPoolExecutor, as_completed

import logging
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

def get_pr_info(repo:str, pr_number:int):
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    response = requests.get(url, headers={
            "Authorization": f"Bearer {get_github_token(repo)}",
            "Accept": "application/vnd.github+json",
        }
    )
    response.raise_for_status()
    pr_data = response.json()
    return  pr_data

def clone_repo(repo:str, token:str, dest:str):
    repo_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(["git", "clone","--depth","50", repo_url, dest], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def checkout_pr_branch(repo_dir:str, pr_ref:int):
    subprocess.run(["git", "fetch", "origin", pr_ref], cwd=repo_dir, check=True)
    subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=repo_dir, check=True)

def compute_diff_stats(repo_dir:str, base_sha:str):
    result = subprocess.run(["git","diff",base_sha], cwd=repo_dir, check=True, capture_output=True, text=True)
    return result.stdout

def summarize_diff(diff:str) -> str:
    files = set()
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            filename = line[6:] if line.startswith("+++ b/") else line[6:]
            files.add(filename)
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return (
        "PR Change Summary:\n"
        f"Changed files: {len(files)}\n"
        f"Lines added: {added}\n"
        f"Lines removed: {removed}\n"
    )
    
def clone_and_analyze_pr(repo: str, pr_number: int, workspace: str) -> str:
    pr = get_pr_info(repo, pr_number)
    commit_sha = pr["head"]["sha"]
    summary_cache_key = f"summary:{repo}:{commit_sha}"
    
    cached_summary = cache_get(summary_cache_key)
    if cached_summary:
        logger.info("Returning cached summary", extra={"commit": commit_sha})
        return cached_summary

    token = get_github_token(repo)
    base_sha = pr["base"]["sha"]
    repo_dir = os.path.join(workspace, "repo")
    graph_cache_key = f"graph:{repo}:{commit_sha}"

    # ---- Phase 2: Clone + diff ----
    # We only clone if we missed the summary cache
    clone_repo(repo, token, repo_dir)
    checkout_pr_branch(repo_dir, pr["head"]["ref"])

    diff = compute_diff_stats(repo_dir, base_sha)
    changed_files = changed_files_from_diff(diff)
    changed_lines = changed_lines_from_diff(diff)

    # ---- Phase 4: Load or build graph ----
    cached_graph = cache_get(graph_cache_key)
    if cached_graph:
        repo_index = deserialize_repo_index(cached_graph["repo_index"])
        symbol_graph = deserialize_symbol_graph(cached_graph["symbol_graph"])
        logger.info("Graph cache hit")
    else:
        repo_index = build_repo_index(repo_dir)
        symbol_graph = build_symbol_graph(repo_dir, repo_index)
        cache_set(graph_cache_key, {
            "repo_index": serialize_repo_index(repo_index),
            "symbol_graph": serialize_symbol_graph(symbol_graph),
        })
        logger.info("Graph cache miss - Built new graph")

    # ---- Phase 3: Detect changed symbols ----
    changed_symbols = []
    for file in changed_files:
        if not file.endswith(".py"): continue
        path = os.path.join(repo_dir, file)
        if not os.path.exists(path): continue

        tree = parse_code(open(path).read())
        symbols = extract_symbols(tree)
        changed_symbols.extend(find_changed_symbols(symbols, changed_lines))

    # ---- Phase 4.5: Impact + confidence ----
    impacts = find_impacts_with_confidence_and_context(
        changed_symbols=changed_symbols,
        symbol_graph=symbol_graph,
        repo_dir=repo_dir,
        repo_index=repo_index,
        base_sha=base_sha,
    )

    # ---- Phase 5: LLM explanations (PARALLELIZED) ----
    def process_impact(impact):
        # Optional: Add gate logic here (e.g. only explain Medium/High)
        explanation = explain_impact(
            changed_symbol=impact["symbol"],
            before_code=impact["before_code"],
            after_code=impact["after_code"],
            impacted_file=impact["file"],
            call_site_code=impact["call_site_code"],
        )
        impact["explanation"] = explanation
        return impact

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_impact, impact): impact for impact in impacts}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"LLM generation failed: {e}")

    # ---- Rendering ----
    summary = summarize_diff(diff)

    if changed_symbols:
        summary += "\nChanged symbols:\n"
        for kind, name in set(changed_symbols):
            summary += f"- {kind}: {name}\n"

    if impacts:
        summary += "\nImpacted files:\n"
        for r in impacts:
            summary += (
                f"- {r['file']} "
                f"(symbol: {r['symbol']}, confidence: {r['label']})\n"
            )
            if r.get("explanation"):
                summary += f"  ↳ {r['explanation']}\n"

    cache_set(summary_cache_key, summary, ttl=3600)
    
    return summary