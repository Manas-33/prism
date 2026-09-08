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
from typing import List, Optional

logger = logging.getLogger(__name__)

# Rules-file names looked for at the root of the target repo, in priority order.
RULES_FILENAMES = (".prism/rules.md", "STYLEGUIDE.md")

# Built-in fallback: generic, locally-checkable Python rules so the feature
# demos honestly even on a repo with no rules file. Each is one assertion the
# LLM can verify against the only evidence it has — the before/after function
# body and the call site — prefixed with a category for context.
_BUILTIN_RULES: List[str] = [
    "Error handling: Never swallow exceptions silently — every `except` block must log the error or re-raise it.",
    "Error handling: Do not signal failure by returning `None` from a public function; raise a typed exception instead.",
    "API design: Never change a public function's return type or parameter list without updating every call site provided.",
    "API design: Functions taking more than three parameters must make them keyword-only (add a bare `*`) so call sites stay unambiguous.",
    "Mutable defaults: Never use a mutable default argument (`[]`, `{}`, `set()`); default to `None` and construct the value inside the body.",
    "Silent behavior: Do not change a function's observable behavior — return value, exceptions raised, or side effects — without a matching update at its call sites.",
]

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


def _rules_from_file(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return parse_rules_markdown(f.read())
    except OSError as e:
        logger.warning("Could not read rules file %s: %s", path, e)
        return []


def get_rules(
    repo: str,
    repo_dir: str,
    *,
    rules_file: Optional[str] = None,
    query_text: Optional[str] = None,
    k: int = 5,
) -> List[str]:
    """Resolve the engineering rules to enforce for one analysis run.

    Resolution order (Stage A):
      1. An explicit `rules_file` override (e.g. CLI `--rules-file`), if it parses.
      2. A rules file in the target repo — `.prism/rules.md`, else `STYLEGUIDE.md`.
      3. The built-in static list.
      4. `[]` (only if the built-in list is emptied) — callers treat empty as
         "no rules" and fall back to today's prompt.

    repo       : repo identifier (unused in Stage A; the scope key in Stage B).
    repo_dir   : local checkout to search for a rules file.
    rules_file : explicit override path, wins over in-repo discovery.
    query_text : ignored in Stage A; Stage B embeds it to retrieve scoped rules.
    k          : ignored in Stage A; Stage B's top-k retrieval size.
    """
    if rules_file:
        rules = _rules_from_file(rules_file)
        if rules:
            logger.info("Loaded %d rule(s) from override %s", len(rules), rules_file)
            return rules
        logger.warning("Rules file %s yielded no rules; falling back", rules_file)

    for candidate in RULES_FILENAMES:
        path = os.path.join(repo_dir, candidate)
        if os.path.isfile(path):
            rules = _rules_from_file(path)
            if rules:
                logger.info("Loaded %d rule(s) from %s", len(rules), candidate)
                return rules

    logger.info("No rules file for %s; using %d built-in rule(s)", repo, len(_BUILTIN_RULES))
    return list(_BUILTIN_RULES)
