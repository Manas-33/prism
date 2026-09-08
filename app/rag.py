"""
Scoped rule retrieval over Qdrant (Stage B of the rules -> RAG feature).

One shared collection (`prism_rules`) holds every repo's rules, partitioned by
an indexed `repo` payload field created as a *tenant index* so Qdrant co-locates
each repo's vectors on disk. Isolation is enforced by funnelling every read
through `retrieve_rules(repo, ...)`, which ALWAYS applies the `repo` filter — a
repo's query can never surface another repo's rules (see the scoping test).

Every Qdrant/embedding call is best-effort: on any failure these functions log a
warning and return a safe empty/no-op value, so analysis never fails because the
vector store is down. `app.rules.get_rules` layers the static/built-in fallback
on top of an empty result.

CLI (manual ingestion):
    python -m app.rag ingest owner/name --file STYLEGUIDE.md
    python -m app.rag ingest owner/name --repo-dir /path/to/checkout
"""
import os
import uuid
import hashlib
import logging
from typing import List, Optional, Tuple

from app.embeddings import embed_documents, embed_query, EMBED_DIM
from app.rules import find_rules_source, parse_rules_markdown

logger = logging.getLogger(__name__)

COLLECTION = "prism_rules"
SCORE_FLOOR = 0.5  # cosine similarity floor; below this a "match" is noise
# Fixed namespace so a (repo, rule) pair always maps to the same point id —
# re-ingesting identical rules updates in place instead of duplicating.
_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

_CLIENT = None


def _client():
    """Cached Qdrant client. Construction is lazy and does not connect, so a
    down server surfaces only when an operation runs (and is caught there)."""
    global _CLIENT
    if _CLIENT is None:
        from qdrant_client import QdrantClient
        loc = os.getenv("QDRANT_URL", "http://localhost:6333")
        _CLIENT = QdrantClient(location=":memory:") if loc == ":memory:" else QdrantClient(url=loc, timeout=5)
    return _CLIENT


def _ensure_collection(client) -> None:
    from qdrant_client import models
    if client.collection_exists(COLLECTION):
        return
    client.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=EMBED_DIM, distance=models.Distance.COSINE),
    )
    # Tenant index on `repo`: same isolation as a collection-per-repo, but one
    # collection with per-repo on-disk locality (Qdrant's multitenancy pattern).
    # Best-effort — the `repo` filter is correct with or without the index.
    try:
        client.create_payload_index(
            COLLECTION,
            field_name="repo",
            field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
    except Exception as e:  # e.g. local in-memory mode
        logger.warning("Could not create tenant index on `repo`: %s", e)


def _repo_filter(repo: str):
    from qdrant_client import models
    return models.Filter(must=[models.FieldCondition(key="repo", match=models.MatchValue(value=repo))])


def ingest_rules(repo: str, rules_source: str, *, source: str = "rules", source_hash: Optional[str] = None) -> int:
    """Chunk a markdown rules doc, embed each rule, and upsert it for `repo`.

    `rules_source` is the markdown text (not a path). Replaces this repo's
    existing rules so a rule removed from the file also disappears from the
    index. Returns the number of rules ingested (0 if the doc has no bullets).
    """
    rules = parse_rules_markdown(rules_source)
    if not rules:
        logger.info("No rules parsed for %s; nothing to ingest", repo)
        return 0

    from qdrant_client import models
    client = _client()
    _ensure_collection(client)
    client.delete(COLLECTION, points_selector=_repo_filter(repo))

    vectors = embed_documents(rules)
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(_NAMESPACE, f"{repo}\n{rule}")),
            vector=vector,
            payload={"repo": repo, "rule_text": rule, "source": source, "source_hash": source_hash or ""},
        )
        for rule, vector in zip(rules, vectors)
    ]
    client.upsert(COLLECTION, points=points)
    logger.info("Ingested %d rule(s) for %s", len(points), repo)
    return len(points)


def retrieve_rules(repo: str, query_text: str, k: int = 5) -> List[str]:
    """Top-k rules for `repo` most relevant to `query_text`, ALWAYS repo-scoped.

    Returns [] on any failure, if nothing is indexed, or if no rule clears the
    score floor — callers then fall back to static rules. `repo` is required and
    always filtered on, so one repo's query can never return another's rules.
    """
    if not repo or not query_text:
        return []
    try:
        client = _client()
        if not client.collection_exists(COLLECTION):
            return []
        result = client.query_points(
            COLLECTION,
            query=embed_query(query_text),
            query_filter=_repo_filter(repo),
            limit=k,
            score_threshold=SCORE_FLOOR,
            with_payload=True,
        )
        return [p.payload["rule_text"] for p in result.points if p.payload and p.payload.get("rule_text")]
    except Exception as e:
        logger.warning("Rule retrieval failed for %s: %s", repo, e)
        return []


def _already_indexed(client, repo: str, source_hash: str) -> bool:
    from qdrant_client import models
    if not client.collection_exists(COLLECTION):
        return False
    points, _ = client.scroll(
        COLLECTION,
        scroll_filter=models.Filter(must=[
            models.FieldCondition(key="repo", match=models.MatchValue(value=repo)),
            models.FieldCondition(key="source_hash", match=models.MatchValue(value=source_hash)),
        ]),
        limit=1,
    )
    return len(points) > 0


def ensure_ingested(repo: str, repo_dir: str, *, rules_file: Optional[str] = None) -> None:
    """Idempotently index a repo's rules file at analysis time.

    No-op if the repo has no rules file, if that exact content is already
    indexed, or if Qdrant is unreachable. Never raises — a vector-store outage
    must not fail analysis. This is the "index on first PR" trigger without any
    GitHub-App-install plumbing.
    """
    source = find_rules_source(repo_dir, rules_file=rules_file)
    if source is None:
        return  # no file -> nothing to ingest (built-in rules are a fallback, not indexed)
    text, source_name = source
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        client = _client()
        if _already_indexed(client, repo, source_hash):
            logger.info("Rules for %s already indexed (unchanged)", repo)
            return
        ingest_rules(repo, text, source=source_name, source_hash=source_hash)
    except Exception as e:
        logger.warning("Auto-ingest skipped for %s: %s", repo, e)


# --------------------------------------------------------------------------- #
# CLI: python -m app.rag ingest owner/name [--file PATH | --repo-dir PATH]
# --------------------------------------------------------------------------- #
def _resolve_source(args) -> Optional[Tuple[str, str]]:
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                return f.read(), os.path.basename(args.file)
        except OSError as e:
            print(f"error: could not read {args.file}: {e}")
            return None
    if args.repo_dir:
        return find_rules_source(args.repo_dir)
    print("error: pass --file PATH or --repo-dir PATH")
    return None


def main(argv=None) -> int:
    import argparse
    import logging as _logging
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog="prism-rag", description="Manage PRism's scoped rule index.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="Embed and index a repo's rules into Qdrant.")
    p_ing.add_argument("repo", help="Repo identifier, e.g. 'owner/name' (the scope key).")
    p_ing.add_argument("--file", metavar="PATH", default=None, help="Rules markdown file to ingest.")
    p_ing.add_argument("--repo-dir", metavar="PATH", default=None, help="Checkout to discover a rules file in.")
    p_ing.add_argument("-v", "--verbose", action="store_true", help="Show INFO logs.")
    args = parser.parse_args(argv)

    _logging.basicConfig(level=_logging.INFO if args.verbose else _logging.WARNING, format="%(levelname)s - %(message)s")

    if args.cmd == "ingest":
        source = _resolve_source(args)
        if source is None:
            return 2
        text, source_name = source
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        n = ingest_rules(args.repo, text, source=source_name, source_hash=source_hash)
        print(f"Ingested {n} rule(s) for {args.repo} from {source_name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
