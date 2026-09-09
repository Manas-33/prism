"""Checks for app.rag: cross-repo scoping isolation (the Stage B guarantee).

Hermetic — runs against an in-memory Qdrant (QDRANT_URL=":memory:") with fake
deterministic embeddings, so it needs no server and no GEMINI_API_KEY. The score
floor is lowered so the `repo` filter (not vector similarity) is what's under
test: retrieving for one repo must never surface another repo's rules.
"""
import os
import random

from tests import check

RULES_A = "## Category\n- alpha rule for A\n- beta rule for A\n"
RULES_B = "## Category\n- gamma rule for B\n- delta rule for B\n"


def _fake_vec(text: str):
    import app.embeddings as emb
    r = random.Random(text)
    return [r.random() for _ in range(emb.EMBED_DIM)]


def run() -> int:
    fails = 0
    os.environ["QDRANT_URL"] = ":memory:"

    import app.rag as rag
    rag._CLIENT = None  # fresh in-memory store
    rag.embed_documents = lambda texts: [_fake_vec(t) for t in texts]  # names bound in rag's namespace
    rag.embed_query = lambda text: _fake_vec(text)
    rag.SCORE_FLOOR = -1.0  # test the repo filter, not similarity ranking

    n_a = rag.ingest_rules("orgA/repo", RULES_A)
    n_b = rag.ingest_rules("orgB/repo", RULES_B)
    fails += check("ingest returns rule count (A=2)", n_a == 2)
    fails += check("ingest returns rule count (B=2)", n_b == 2)

    res_a = rag.retrieve_rules("orgA/repo", "alpha", k=10)
    res_b = rag.retrieve_rules("orgB/repo", "gamma", k=10)

    fails += check("repo A retrieval is non-empty", len(res_a) > 0)
    fails += check("repo A returns only A's rules", all("for A" in r for r in res_a))
    fails += check("repo B returns only B's rules", all("for B" in r for r in res_b))
    fails += check("no B rule leaks into A", not any("for B" in r for r in res_a))
    fails += check("no A rule leaks into B", not any("for A" in r for r in res_b))
    fails += check("unknown repo retrieves nothing", rag.retrieve_rules("orgC/none", "alpha", k=10) == [])

    # Re-ingesting identical content replaces rather than duplicates.
    rag.ingest_rules("orgA/repo", RULES_A)
    fails += check("re-ingest does not duplicate (still 2)", len(rag.retrieve_rules("orgA/repo", "alpha", k=10)) == 2)

    # --- builtin scope: defaults are shared with every repo, never vice versa ---
    rag.ingest_rules(rag.BUILTIN_SCOPE, "## Defaults\n- builtin epsilon rule\n")
    res_a2 = rag.retrieve_rules("orgA/repo", "alpha", k=10)
    fails += check("repo A sees its rules plus builtins", any("epsilon" in r for r in res_a2) and any("for A" in r for r in res_a2))
    fails += check("repo A still sees no B rules alongside builtins", not any("for B" in r for r in res_a2))
    fails += check("repo with no own rules gets builtins", rag.retrieve_rules("orgC/none", "alpha", k=10) == ["Defaults: builtin epsilon rule"])

    return fails
