"""
The engineering-rules seam (Stage A of the rules -> RAG feature).

`get_rules(...)` is the single point every caller uses to obtain the internal
engineering rules to enforce for one analysis run. Stage A resolves them from
the target repo's own rules file (falling back to a small built-in list); Stage
B will swap the *body* of this function for scoped Qdrant retrieval WITHOUT
changing its signature or any call site — that seam is the whole point of the
module. `query_text` / `k` are accepted and ignored today so Stage B's
per-impact retrieval slots in with no signature change.

A "rule" is a short, imperative, locally-checkable statement (one assertion per
rule), returned as a flat list of strings prefixed with their source heading:

    "Error handling: Never swallow exceptions silently — every `except` must log or re-raise."

See local.md ("What the rules look like") for the shape and rationale.
"""
import os
import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Rules-file names looked for at the root of the target repo, in priority order.
RULES_FILENAMES = (".prism/rules.md", "STYLEGUIDE.md")

# PRism's default ruleset: generic, locally-checkable Python rules enforced on
# any repo that has no rules file of its own (a repo's `.prism/rules.md` /
# STYLEGUIDE.md layers on top — see app.rag). Each rule is one assertion the
# LLM can verify against the only evidence it has — the before/after function
# body and the call site. The (id, heading, bullet) shape keeps ids stable for
# the retrieval benchmark (eval/rag_cases.py), which is labeled against this
# exact corpus.
DEFAULT_RULES: List[Tuple[str, str, str]] = [
    # Error handling
    ("EH1", "Error handling", "Never swallow exceptions silently — every `except` block must log the error or re-raise it."),
    ("EH2", "Error handling", "Do not signal failure by returning `None` from a public function; raise a typed exception instead."),
    ("EH3", "Error handling", "Catch specific exception types; a bare `except:` or `except Exception:` is not allowed outside process entry points."),
    ("EH4", "Error handling", "Never use `assert` to validate arguments or user input at runtime; asserts are stripped under `python -O`."),
    # API design
    ("API1", "API design", "Never change a public function's parameter list or return type without updating every call site in the same change."),
    ("API2", "API design", "Functions taking more than three parameters must make the extras keyword-only (add a bare `*`)."),
    ("API3", "API design", "Do not rename a public function or parameter without keeping a deprecated alias for one release."),
    ("API4", "API design", "Public functions must declare explicit parameters; `**kwargs` is not a substitute for a real signature."),
    # Mutability
    ("MUT1", "Mutability", "Never use a mutable default argument (`[]`, `{}`, `set()`); default to `None` and construct the value inside the body."),
    ("MUT2", "Mutability", "Do not mutate objects the caller passed in; build and return a new value instead."),
    ("MUT3", "Mutability", "Module-level mutable state must not be mutated from request handlers or at import time."),
    # Logging
    ("LOG1", "Logging", "Use lazy `%s` formatting in logging calls, never f-strings or `.format()`, so the message is only built when emitted."),
    ("LOG2", "Logging", "Never log secrets, API tokens, passwords, or full request payloads."),
    ("LOG3", "Logging", "Degraded or fallback behavior the operator must know about is logged at WARNING or above, not INFO."),
    # Concurrency
    ("CON1", "Concurrency", "Never share one database connection or session across threads; acquire one per task from the pool."),
    ("CON2", "Concurrency", "Check-then-act sequences on shared state must hold a lock across both steps."),
    ("CON3", "Concurrency", "Never call blocking I/O inside an `async def`; use the async client or hand it to an executor."),
    # Security
    ("SEC1", "Security", "Never build SQL by string formatting or concatenation; use parameterized queries."),
    ("SEC2", "Security", "Never invoke `subprocess` with `shell=True` on anything derived from user input."),
    ("SEC3", "Security", "Never deserialize untrusted data with `pickle` or evaluate it with `eval`/`exec`."),
    ("SEC4", "Security", "Compare secrets and signatures with `hmac.compare_digest`, never with `==`."),
    # Resources
    ("RES1", "Resources", "Open files, sockets, and connections with a context manager; never rely on garbage collection to close them."),
    ("RES2", "Resources", "Every outbound network call must set an explicit timeout."),
    ("RES3", "Resources", "Retries must be bounded and use backoff; never retry in a tight loop."),
    # Datetime
    ("DT1", "Datetime", "Always construct timezone-aware datetimes; naive helpers like `datetime.utcnow()` are banned."),
    ("DT2", "Datetime", "Store and compare timestamps in UTC; convert to local time only at the display edge."),
    # Performance
    ("PERF1", "Performance", "Do not build strings with `+=` inside a loop; collect parts and `join` them."),
    ("PERF2", "Performance", "Do not read a whole file into memory when streaming it line by line suffices."),
    ("PERF3", "Performance", "Never issue one query per item of a collection (N+1); batch the fetch."),
    # Style
    ("STY1", "Style", "Boolean parameters must be passed by keyword at the call site, never positionally."),
    ("STY2", "Style", "Do not shadow Python builtins (`id`, `type`, `list`, `dict`) with local or parameter names."),
    ("STY3", "Style", "Compare against `None` with `is` / `is not`, never with `==`."),
]

# Flat "Heading: bullet" strings — the shape parse_rules_markdown produces and
# every consumer (prompt assembly, Qdrant ingestion) expects.
_BUILTIN_RULES: List[str] = [f"{heading}: {bullet}" for _, heading, bullet in DEFAULT_RULES]


def builtin_rules_markdown() -> str:
    """The default ruleset rendered as a markdown doc, so builtin ingestion
    goes through the same parse path as a repo's own rules file."""
    lines: List[str] = []
    current = None
    for _, heading, bullet in DEFAULT_RULES:
        if heading != current:
            lines.append(f"## {heading}")
            current = heading
        lines.append(f"- {bullet}")
    return "\n".join(lines) + "\n"

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")


def parse_rules_markdown(text: str) -> List[str]:
    """Flatten a markdown rules doc into one string per bullet.

    Each bullet is prefixed with the nearest preceding heading so the LLM keeps
    the category context (and, in Stage B, so that context travels with the
    chunk into Qdrant). Headings themselves are context, not rules; only bullets
    become rules. Files with no headings yield bare, un-prefixed rules.
    """
    rules: List[str] = []
    heading: Optional[str] = None
    for line in text.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            heading = h.group(1).strip()
            continue
        b = _BULLET_RE.match(line)
        if b:
            body = b.group(1).strip()
            if body:
                rules.append(f"{heading}: {body}" if heading else body)
    return rules


def find_rules_source(repo_dir: str, *, rules_file: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Return (markdown_text, source_name) for the repo's rules doc, or None.

    Resolution: an explicit `rules_file` override, else `.prism/rules.md`, else
    `STYLEGUIDE.md`, taking the first that exists. Returns None when there is no
    rules file — the built-in rules are a fallback and are not sourced from disk
    (nor ingested into Qdrant). Shared by get_rules (Stage A fallback) and
    app.rag (ingestion), so both agree on what "the repo's rules" are.
    """
    candidates: List[Tuple[str, str]] = []
    if rules_file:
        candidates.append((rules_file, os.path.basename(rules_file)))
    for name in RULES_FILENAMES:
        candidates.append((os.path.join(repo_dir, name), name))
    for path, source_name in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read(), source_name
            except OSError as e:
                logger.warning("Could not read rules file %s: %s", path, e)
    return None


def get_rules(
    repo: str,
    repo_dir: str,
    *,
    rules_file: Optional[str] = None,
    query_text: Optional[str] = None,
    k: int = 5,
) -> List[str]:
    """Resolve the engineering rules to enforce for one impact (or run).

    Resolution order:
      1. Stage B — scoped Qdrant retrieval, when `query_text` is given and the
         repo has rules indexed. Always repo-scoped (see app.rag).
      2. Stage A — an explicit `rules_file`, else `.prism/rules.md`, else
         `STYLEGUIDE.md` in the checkout.
      3. The built-in static list.
      4. `[]` (only if the built-in list is emptied) — callers treat empty as
         "no rules" and fall back to today's prompt.

    Retrieval failures or an empty index fall through to steps 2-3, so the
    feature degrades cleanly to Stage A behaviour when Qdrant is unavailable.

    repo       : repo identifier + the Stage B retrieval scope key.
    repo_dir   : local checkout to search for a rules file.
    rules_file : explicit override path, wins over in-repo discovery.
    query_text : Stage B — the changed code/call-site text to retrieve against.
    k          : Stage B — top-k retrieval size.
    """
    if query_text:
        try:
            from app.rag import retrieve_rules
            retrieved = retrieve_rules(repo, query_text, k=k)
            if retrieved:
                logger.info("Retrieved %d scoped rule(s) for %s", len(retrieved), repo)
                return retrieved
        except Exception as e:
            logger.warning("Rule retrieval unavailable (%s); using static rules", e)

    source = find_rules_source(repo_dir, rules_file=rules_file)
    if source:
        rules = parse_rules_markdown(source[0])
        if rules:
            logger.info("Loaded %d rule(s) from %s", len(rules), source[1])
            return rules

    logger.info("No rules file for %s; using %d built-in rule(s)", repo, len(_BUILTIN_RULES))
    return list(_BUILTIN_RULES)
