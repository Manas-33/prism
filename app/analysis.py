"""
The analysis core, factored out of the webhook/PR wrapper.

`analyze_impacts` operates on a repo that is already on disk (checked out at
head_sha, with base_sha reachable) and returns structured impacts. It knows
nothing about GitHub webhooks, PR numbers, or comment posting, so it can be
driven equally by the online worker, a CLI, the eval harness, or a bug-hunt
runner.
"""
import os
import logging
from dataclasses import dataclass
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.git_ops import compute_diff
from app.static_analysis import (
    changed_files_from_diff,
    changed_lines_by_file,
    parse_code,
    extract_symbols,
    find_changed_symbols,
    filter_code_lines,
    is_substantive_change,
)
from app.dependency_graph import (
    build_symbol_graph,
    find_impacts_with_confidence_and_context,
    git_show_file,
)
from app.repo_index import build_repo_index
from app.llm_service import explain_impact
from app.cache import cache_get, cache_set
from app.models import (
    serialize_repo_index,
    deserialize_repo_index,
    serialize_symbol_graph,
    deserialize_symbol_graph,
)

logger = logging.getLogger(__name__)

LLM_MAX_WORKERS = 5


@dataclass
class AnalysisResult:
    """Structured output of one analysis run — the contract every caller shares."""
    repo: str
    base_sha: str
    head_sha: str
    diff: str
    changed_files: List[str]
    changed_symbols: List[Tuple[str, str]]  # (kind, name)
    impacts: List[dict]

    def to_dict(self) -> dict:
        """JSON-serializable view (drops the raw diff; keeps the structured impacts)."""
        return {
            "repo": self.repo,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "changed_files": self.changed_files,
            "changed_symbols": [list(cs) for cs in self.changed_symbols],
            "impacts": self.impacts,
        }


def _load_or_build_graph(repo_dir: str, repo: str, head_sha: str, use_cache: bool):
    graph_cache_key = f"graph:{repo}:{head_sha}"

    if use_cache:
        cached_graph = cache_get(graph_cache_key)
        if cached_graph:
            logger.info("Graph cache hit")
            return (
                deserialize_repo_index(cached_graph["repo_index"]),
                deserialize_symbol_graph(cached_graph["symbol_graph"]),
            )

    repo_index = build_repo_index(repo_dir)
    symbol_graph = build_symbol_graph(repo_dir, repo_index)

    if use_cache:
        cache_set(graph_cache_key, {
            "repo_index": serialize_repo_index(repo_index),
            "symbol_graph": serialize_symbol_graph(symbol_graph),
        })
        logger.info("Graph cache miss - built new graph")

    return repo_index, symbol_graph


def _detect_changed_symbols(repo_dir: str, changed_by_file, base_sha):
    """Return (kind, name, file) triples for symbols whose lines changed.

    Carrying the defining file lets impact matching key on symbol identity
    (file:kind:name) rather than bare name.
    """
    triples = []
    for file, lines in changed_by_file.items():
        if not file.endswith(".py") or not lines:
            continue
        path = os.path.join(repo_dir, file)
        if not os.path.exists(path):
            continue

        with open(path, "r", encoding="utf-8") as f:
            head_src = f.read()
        head_tree = parse_code(head_src)

        # First pass: drop obviously-cosmetic changed lines (blank/comment/docstring).
        code_lines = filter_code_lines(head_tree, head_src, lines)
        if not code_lines:
            continue

        head_symbols = extract_symbols(head_tree)
        candidates = find_changed_symbols(head_symbols, code_lines)
        if not candidates:
            continue

        # Second pass: drop symbols whose base->head change is only comments,
        # docstrings, or type annotations (can't break callers).
        base_src = git_show_file(repo_dir, base_sha, file)
        base_symbols = extract_symbols(parse_code(base_src)) if base_src else {"functions": [], "classes": []}
        for kind, name in candidates:
            if is_substantive_change(base_src, base_symbols, head_src, head_symbols, kind, name):
                triples.append((kind, name, file))
    return triples


def _explain_impacts(impacts: List[dict]) -> None:
    """Attach an LLM explanation to each impact, in parallel. Mutates in place."""
    def process_impact(impact):
        impact["explanation"] = explain_impact(
            changed_symbol=impact["symbol"],
            before_code=impact["before_code"],
            after_code=impact["after_code"],
            impacted_file=impact["file"],
            call_site_code=impact["call_site_code"],
        )
        return impact

    with ThreadPoolExecutor(max_workers=LLM_MAX_WORKERS) as executor:
        futures = {executor.submit(process_impact, impact): impact for impact in impacts}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"LLM generation failed: {e}")


def analyze_impacts(
    repo_dir: str,
    repo: str,
    base_sha: str,
    head_sha: str,
    *,
    explain: bool = True,
    use_cache: bool = True,
) -> AnalysisResult:
    """
    Analyze the change base_sha -> head_sha for a repo already on disk.

    repo_dir : local checkout, with HEAD at head_sha and base_sha reachable.
    repo     : identifier used for cache keys (e.g. "owner/name").
    explain  : run the LLM impact explanations (needs GEMINI_API_KEY). Off for
               graph-only runs like precision/recall eval.
    use_cache: use the Redis graph cache. Off for offline runs with no Redis.
    """
    # ---- Diff ----
    diff = compute_diff(repo_dir, base_sha)
    changed_files = changed_files_from_diff(diff)
    changed_by_file = changed_lines_by_file(diff)

    # ---- Graph (cached by head SHA) ----
    repo_index, symbol_graph = _load_or_build_graph(repo_dir, repo, head_sha, use_cache)

    # ---- Changed symbols (identity = defining file:kind:name) ----
    changed_triples = _detect_changed_symbols(repo_dir, changed_by_file, base_sha)
    changed_ids = {f"{file}:{kind}:{name}" for (kind, name, file) in changed_triples}

    # De-duplicated (kind, name) for reporting, preserving first-seen order.
    seen = set()
    changed_symbols = []
    for kind, name, _file in changed_triples:
        if (kind, name) not in seen:
            seen.add((kind, name))
            changed_symbols.append((kind, name))

    # ---- Impacts + confidence + context ----
    impacts = find_impacts_with_confidence_and_context(
        changed_ids=changed_ids,
        symbol_graph=symbol_graph,
        repo_dir=repo_dir,
        repo_index=repo_index,
        base_sha=base_sha,
    )

    # ---- LLM explanations (optional) ----
    if explain:
        _explain_impacts(impacts)

    return AnalysisResult(
        repo=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        diff=diff,
        changed_files=changed_files,
        changed_symbols=changed_symbols,
        impacts=impacts,
    )
