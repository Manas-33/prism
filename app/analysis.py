"""
The analysis core, factored out of the webhook/PR wrapper.

`analyze_impacts` operates on a repo that is already on disk (checked out at
head_sha, with base_sha reachable) and returns structured impacts. It knows
nothing about GitHub webhooks, PR numbers, or comment posting, so it can be
driven equally by the online worker, a CLI, the eval harness, or a bug-hunt
runner.
"""
import ast
import os
import textwrap
import time
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
from app.rules import get_rules
from app.rag import ensure_ingested
from app.cache import cache_get, cache_set
from app.metrics import ANALYSIS_DURATION, IMPACTS_DETECTED
from app.models import (
    serialize_repo_index,
    deserialize_repo_index,
    serialize_symbol_graph,
    deserialize_symbol_graph,
)

logger = logging.getLogger(__name__)

LLM_MAX_WORKERS = 5
RETRIEVAL_K = 5


def _parse_snippet(code: str):
    """Best-effort AST for a code snippet (dedented; may be a method pulled out
    of a class). Returns None when the fragment doesn't parse — structural
    facts are then simply skipped."""
    if not code:
        return None
    try:
        return ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None


def _first_def(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _param_names(fn) -> list:
    a = fn.args
    return [p.arg for p in (a.posonlyargs + a.args + a.kwonlyargs)]


def _calls_by_callee(tree) -> dict:
    """Map dotted callee name -> set of keyword-argument names used, across
    every call in the snippet."""
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        try:
            name = ast.unparse(node.func)
        except Exception:
            continue
        out.setdefault(name, set()).update(kw.arg for kw in node.keywords if kw.arg)
    return out


def _structural_summary(before: str, after: str, call_site: str) -> List[str]:
    """Plain-prose facts about *structural* aspects of the change — renames,
    signature edits, dropped keyword arguments, positional booleans, f-string
    log messages. Embeddings rank rules by topical similarity, and these
    violations have no vocabulary in the raw code for a prose rule to match
    (a missing `timeout=` is an absence; a rename is only visible in the
    before/after *diff*), so the query states them explicitly."""
    facts: List[str] = []
    b_tree, a_tree = _parse_snippet(before), _parse_snippet(after)

    if b_tree is not None and a_tree is not None:
        b_fn, a_fn = _first_def(b_tree), _first_def(a_tree)
        if b_fn is not None and a_fn is not None:
            if b_fn.name != a_fn.name:
                facts.append(f"function renamed from `{b_fn.name}` to `{a_fn.name}`")
            b_params, a_params = _param_names(b_fn), _param_names(a_fn)
            added = [p for p in a_params if p not in b_params]
            removed = [p for p in b_params if p not in a_params]
            if added:
                facts.append("parameter list changed: `%s` added to the function signature" % "`, `".join(added))
            if removed:
                facts.append("parameter list changed: `%s` removed from the function signature" % "`, `".join(removed))
            if a_fn.args.kwarg is not None and b_fn.args.kwarg is None:
                facts.append("signature now takes `**kwargs` instead of named parameters")
        # Keyword arguments dropped from a call that exists on both sides
        # (e.g. `timeout=` no longer passed to a network call).
        b_calls, a_calls = _calls_by_callee(b_tree), _calls_by_callee(a_tree)
        for callee, b_kws in b_calls.items():
            dropped = b_kws - a_calls.get(callee, b_kws)
            for kw in sorted(dropped):
                facts.append(f"keyword argument `{kw}=` no longer passed in the call to `{callee}`")

    for tree in (a_tree, _parse_snippet(call_site)):
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if any(isinstance(arg, ast.Constant) and arg.value in (True, False) for arg in node.args):
                facts.append("a boolean literal is passed as a positional argument in a call")
            func = node.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and "log" in func.value.id.lower()
                    and any(isinstance(arg, ast.JoinedStr) for arg in node.args)):
                facts.append("a logging call builds its message with an f-string")

    # Deduplicate, preserving order.
    return list(dict.fromkeys(facts))


def _impact_query_text(impact: dict) -> str:
    """Build the Stage B retrieval query for one impact from its changed
    symbol, before/after bodies, and call site — so retrieval pulls the rules
    relevant to *this* change (e.g. a try/except diff pulls error-handling
    rules). Appends a structural-change summary so rules about renames,
    signature edits, and missing arguments are reachable too (raw code carries
    no embedding signal for those — see _structural_summary)."""
    before = impact.get("before_code", "")
    after = impact.get("after_code", "")
    call_site = impact.get("call_site_code", "")
    parts = [impact.get("symbol", ""), before, after, call_site]
    facts = _structural_summary(before, after, call_site)
    if facts:
        parts.append("Change summary:\n" + "\n".join(f"- {f}" for f in facts))
    return "\n".join(p for p in parts if p)


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


def _resolve_rules_cached(impact, repo, repo_dir, head_sha, use_cache, rules_file):
    """Resolve one impact's rules, cached per (repo, head_sha, symbol) in Redis.

    The head SHA pins the code, so a symbol's retrieval result is stable for that
    SHA — caching it avoids re-embedding + re-querying Qdrant when the same PR
    head is re-analyzed. Skipped entirely when use_cache is False (offline runs
    with no Redis). Mirrors the graph cache's key/TTL pattern.
    """
    cache_key = f"rules:{repo}:{head_sha}:{impact['symbol']}"
    if use_cache:
        cached = cache_get(cache_key)
        if cached is not None:
            logger.info("Rules cache hit for %s", impact["symbol"])
            return cached
    rules = get_rules(
        repo, repo_dir,
        rules_file=rules_file,
        query_text=_impact_query_text(impact),
        k=RETRIEVAL_K,
    )
    if use_cache:
        cache_set(cache_key, rules)
    return rules


def _explain_impacts(impacts: List[dict], *, repo: str, repo_dir: str, head_sha: str,
                     use_cache: bool, rules_file: str | None = None) -> None:
    """Attach an LLM explanation to each impact, in parallel. Mutates in place.

    Each impact resolves its OWN rules (Stage B): get_rules embeds the impact's
    changed code + call site and retrieves the rules scoped to that repo and
    relevant to that change (cached per symbol by head SHA). When Qdrant is
    empty/unreachable this degrades to the repo's static/built-in rules, and an
    empty result leaves the prompt rule-free — so the path is safe with or
    without a vector store.
    """
    def process_impact(impact):
        rules = _resolve_rules_cached(impact, repo, repo_dir, head_sha, use_cache, rules_file)
        impact["explanation"] = explain_impact(
            changed_symbol=impact["symbol"],
            before_code=impact["before_code"],
            after_code=impact["after_code"],
            impacted_file=impact["file"],
            call_site_code=impact["call_site_code"],
            rules=rules,
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
    rules_file: str | None = None,
) -> AnalysisResult:
    """
    Analyze the change base_sha -> head_sha for a repo already on disk.

    repo_dir : local checkout, with HEAD at head_sha and base_sha reachable.
    repo     : identifier used for cache keys (e.g. "owner/name").
    explain  : run the LLM impact explanations (needs GEMINI_API_KEY). Off for
               graph-only runs like precision/recall eval.
    use_cache: use the Redis graph cache. Off for offline runs with no Redis.
    rules_file: optional override path for the engineering rules to enforce;
               otherwise rules are discovered from the repo (see app.rules).
               Only consulted when explain=True.
    """
    start = time.perf_counter()

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
        # Stage B: index this repo's rules once (idempotent, best-effort), then
        # each impact retrieves the rules scoped to its own changed code. Rules
        # only affect the LLM prompt, so graph-only/eval runs (explain=False) are
        # untouched — which is why this cannot move the precision/recall numbers.
        ensure_ingested(repo, repo_dir, rules_file=rules_file)
        _explain_impacts(
            impacts, repo=repo, repo_dir=repo_dir,
            head_sha=head_sha, use_cache=use_cache, rules_file=rules_file,
        )

    result = AnalysisResult(
        repo=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        diff=diff,
        changed_files=changed_files,
        changed_symbols=changed_symbols,
        impacts=impacts,
    )
    ANALYSIS_DURATION.observe(time.perf_counter() - start)
    IMPACTS_DETECTED.observe(len(impacts))
    return result
