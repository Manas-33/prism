"""Checks for the per-impact rules cache in app.analysis._resolve_rules_cached.

Hermetic — Redis (cache_get/cache_set) and get_rules are monkeypatched in
analysis's namespace, so no Redis/Qdrant/LLM is touched. Originals are restored
so test ordering can't leak.
"""
import app.analysis as analysis
from tests import check

IMPACT = {"symbol": "add", "before_code": "a+b", "after_code": "a-b",
          "file": "x.py", "call_site_code": "add()"}


def run() -> int:
    fails = 0
    orig = (analysis.cache_get, analysis.cache_set, analysis.get_rules)
    store: dict = {}
    calls = {"n": 0}

    analysis.cache_get = lambda k: store.get(k)
    analysis.cache_set = lambda k, v, ttl=None: store.__setitem__(k, v)

    def fake_get_rules(repo, repo_dir, *, rules_file=None, query_text=None, k=5):
        calls["n"] += 1
        return ["Category: some rule"]
    analysis.get_rules = fake_get_rules

    try:
        # miss -> computes and stores under the documented key
        r1 = analysis._resolve_rules_cached(IMPACT, "o/n", "/repo", "HEAD123", True, None)
        fails += check("miss returns rules", r1 == ["Category: some rule"])
        fails += check("key is rules:{repo}:{head}:{symbol}", "rules:o/n:HEAD123:add" in store)
        fails += check("miss called get_rules once", calls["n"] == 1)

        # hit -> served from cache, no second retrieval
        r2 = analysis._resolve_rules_cached(IMPACT, "o/n", "/repo", "HEAD123", True, None)
        fails += check("hit returns same rules", r2 == ["Category: some rule"])
        fails += check("hit did not call get_rules again", calls["n"] == 1)

        # different head SHA -> different key -> recomputes
        analysis._resolve_rules_cached(IMPACT, "o/n", "/repo", "HEAD999", True, None)
        fails += check("new head SHA recomputes", calls["n"] == 2)

        # use_cache=False -> always computes, stores nothing
        store.clear(); calls["n"] = 0
        analysis._resolve_rules_cached(IMPACT, "o/n", "/repo", "HEAD123", False, None)
        analysis._resolve_rules_cached(IMPACT, "o/n", "/repo", "HEAD123", False, None)
        fails += check("use_cache=False computes each time", calls["n"] == 2)
        fails += check("use_cache=False stores nothing", store == {})
    finally:
        analysis.cache_get, analysis.cache_set, analysis.get_rules = orig

    return fails
