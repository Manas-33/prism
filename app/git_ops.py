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
    find_impacted_files,
)
from app.repo_index import build_repo_index
from app.cache import cache_get, cache_set
from app.models import serialize_repo_index, deserialize_repo_index, serialize_symbol_graph, deserialize_symbol_graph
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
    
def clone_and_analyze_pr(repo:str, pr_number:int, workspace:str) -> str:
    # Get Token and PR info
    token = get_github_token(repo)
    pr = get_pr_info(repo, pr_number)
    
    base_sha = pr["base"]["sha"]
    repo_dir = os.path.join(workspace, "repo")
    commit_sha = pr["head"]["sha"]
    cache_key = f"{repo}:{commit_sha}"
    
    # Clone and checkout PR branch
    clone_repo(repo, token, repo_dir)
    checkout_pr_branch(repo_dir, pr["head"]["ref"])
    
    # Compute diff
    diff = compute_diff_stats(repo_dir, base_sha)
    changed_files = changed_files_from_diff(diff)
    changed_lines = changed_lines_from_diff(diff)
    
    cached = cache_get(cache_key)
    if cached:
        repo_index = deserialize_repo_index(cached["repo_index"])
        symbol_graph = deserialize_symbol_graph(cached["symbol_graph"])
        logger.info("Cache hit", extra={"commit": commit_sha})
    else:   
        # Build indexes and graphs
        repo_index = build_repo_index(repo_dir)
        file_graph = build_file_graph(repo_index)
        symbol_graph = build_symbol_graph(repo_index,repo_dir)
        logger.info("Cache miss - computed index and graphs", extra={"commit": commit_sha})
        cache_set(cache_key, {
            "repo_index": serialize_repo_index(repo_index),
            "symbol_graph": serialize_symbol_graph(symbol_graph),
        })

    # Analyze changes for impacted symbols and files
    changed_symbols = []

    for file in changed_files:
        if not file.endswith(".py"):
            continue

        path = os.path.join(repo_dir, file)
        if not os.path.exists(path):
            continue

        code = open(path).read()
        tree = parse_code(code)

        symbols = extract_symbols(tree)
        imports = extract_imports(tree)

        hits = find_changed_symbols(symbols, changed_lines)
        changed_symbols.extend(hits)
    
    # Find impacted files
    impacted_files = find_impacted_files(
        changed_symbols,
        symbol_graph,
    )
    summary = summarize_diff(diff)
    # Add detailed changed symbols and impacted files
    if changed_symbols:
        summary += "\nChanged symbols:\n"
        for kind, name in set(changed_symbols):
            summary += f"- {kind}: {name}\n"
            
    if impacted_files:
        summary += "\nImpacted files:\n"
        for f in impacted_files:
            summary += f"- {f}\n"
    return summary