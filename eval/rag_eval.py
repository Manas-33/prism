"""
RAG retrieval benchmark: does `retrieve_rules` surface the violated rule?

Runs the PRODUCTION path end to end — `parse_rules_markdown` -> Gemini
embeddings -> Qdrant (in-memory by default) -> `retrieve_rules` with its
top-k and score floor — against the labeled cases in eval/rag_cases.py.
Each query is assembled exactly like app.analysis._impact_query_text builds
it for a real impact.

Metrics:
  * hit@k  — the violated rule appears in the top k retrieved rules
             (k=1, 3, and 5; 5 is what production passes to the LLM)
  * MRR    — mean reciprocal rank of the violated rule
  * chance — expected hit@k for a random ranking of the corpus, for context

Usage (from the repo root):
    python -m eval.rag_eval            # in-memory Qdrant, ~62 embedding calls
    QDRANT_URL=http://localhost:6333 python -m eval.rag_eval

Writes eval/rag_results.json next to this file.
"""
import json
import logging
import math
import os
import sys
import time

# In-memory Qdrant unless the caller points at a real one; must be set before
# app.rag constructs its client.
os.environ.setdefault("QDRANT_URL", ":memory:")

from dotenv import load_dotenv

from eval.rag_cases import CASES, RULES, rule_text

REPO = "prism-eval/rag-benchmark"
K = 5  # matches app.analysis.RETRIEVAL_K


def wilson(successes: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a proportion (same choice as the
    precision eval — behaves sensibly at small n)."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def chance_hit_at_k(k: int, n_gold: int, corpus: int) -> float:
    """P(at least one of n_gold rules lands in a random top-k of the corpus)."""
    miss = 1.0
    for i in range(k):
        miss *= (corpus - n_gold - i) / (corpus - i)
    return 1.0 - miss


def build_query(case: dict) -> str:
    """Build the query with the REAL production builder (including its
    structural-change summary) by shaping the case as an impact dict."""
    from app.analysis import _impact_query_text
    return _impact_query_text({
        "symbol": case["symbol"],
        "before_code": case["before"],
        "after_code": case["after"],
        "call_site_code": case["call_site"],
    })


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        print("error: GEMINI_API_KEY is not set (needed for embeddings)")
        return 2

    from app.rag import ensure_builtin_ingested, ingest_rules, retrieve_rules

    corpus = len(RULES)
    # The corpus IS the shipped default ruleset: ingest it exactly the way
    # production does, under the reserved builtin scope.
    ensure_builtin_ingested()
    probe = retrieve_rules(REPO, "never swallow exceptions silently", k=corpus)
    if not probe:
        print("error: builtin ingestion produced no retrievable rules (check GEMINI_API_KEY / Qdrant)")
        return 2
    print(f"Ingested {corpus} builtin rules (QDRANT_URL={os.getenv('QDRANT_URL')})")

    # Scoping sanity check: another tenant's rules must never reach REPO.
    ingest_rules("other/tenant", "## Marker\n- LEAKED-MARKER-RULE do not retrieve this\n")
    leak = [r for r in retrieve_rules(REPO, "LEAKED-MARKER-RULE do not retrieve this", k=corpus) if "LEAKED-MARKER" in r]
    if leak:
        print("error: cross-repo scoping leak — another tenant's rule reached the benchmark repo")
        return 2

    results = []
    for case in CASES:
        retrieved = retrieve_rules(REPO, build_query(case), k=K)
        gold_texts = {rule_text(rid) for rid in case["gold"]}
        rank = next((i + 1 for i, r in enumerate(retrieved) if r in gold_texts), None)
        results.append({
            "id": case["id"],
            "gold": case["gold"],
            "rank": rank,
            "n_retrieved": len(retrieved),
            "retrieved_top3": retrieved[:3],
        })
        marker = f"rank {rank}" if rank else ("MISS" if retrieved else "MISS (nothing cleared score floor)")
        print(f"  {case['id']:<24} {marker}")
        time.sleep(0.3)  # stay well under the embeddings rate limit

    n = len(results)
    hits = {k: sum(1 for r in results if r["rank"] is not None and r["rank"] <= k) for k in (1, 3, 5)}
    mrr = sum(1.0 / r["rank"] for r in results if r["rank"]) / n
    empty = sum(1 for r in results if r["n_retrieved"] == 0)

    print(f"\n=== RAG retrieval benchmark — n={n} queries over {corpus} rules ===")
    for k in (1, 3, 5):
        lo, hi = wilson(hits[k], n)
        chance = chance_hit_at_k(k, 1, corpus)
        print(f"hit@{k}: {hits[k]}/{n} = {hits[k]/n:.2%}   [Wilson 95%: {lo:.2f}, {hi:.2f}]   (chance: {chance:.1%})")
    print(f"MRR: {mrr:.3f}")
    print(f"queries with nothing above the {0.5} score floor: {empty}/{n}")

    out_path = os.path.join(os.path.dirname(__file__), "rag_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "n_queries": n, "corpus_rules": corpus, "k": K,
            "hit_at": {str(k): hits[k] / n for k in (1, 3, 5)},
            "wilson_95": {str(k): wilson(hits[k], n) for k in (1, 3, 5)},
            "mrr": mrr, "empty_retrievals": empty,
            "cases": results,
        }, f, indent=2)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
