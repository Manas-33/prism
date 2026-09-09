"""Checks for app.rules: markdown parsing + get_rules discovery/fallback."""
import os
import tempfile

from tests import check
from app.rules import parse_rules_markdown, get_rules, _BUILTIN_RULES

SAMPLE = """# Engineering Rules

## Error handling
- Never swallow exceptions silently.
- Public functions must not return `None` on failure.

## API design
- Functions with more than 3 parameters must use keyword-only arguments.
"""


def _write(dirpath: str, rel: str, text: str) -> str:
    path = os.path.join(dirpath, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run() -> int:
    fails = 0

    # --- parser: bullets become rules, prefixed by nearest heading ---
    parsed = parse_rules_markdown(SAMPLE)
    fails += check("parser: extracts one rule per bullet", len(parsed) == 3)
    fails += check(
        "parser: prefixes bullet with nearest heading",
        parsed[0] == "Error handling: Never swallow exceptions silently.",
    )
    fails += check(
        "parser: heading switches with section",
        parsed[2].startswith("API design: "),
    )
    fails += check(
        "parser: headings themselves are not rules",
        all("Engineering Rules:" not in r for r in parsed),
    )

    # --- parser: no headings -> bare rules; numbered bullets supported ---
    flat = parse_rules_markdown("- alpha\n* beta\n1. gamma\n")
    fails += check("parser: bare bullets, no prefix", flat == ["alpha", "beta", "gamma"])
    fails += check("parser: ignores non-bullet prose", parse_rules_markdown("just text") == [])

    with tempfile.TemporaryDirectory() as d:
        # --- discovery: STYLEGUIDE.md when it's the only file ---
        _write(d, "STYLEGUIDE.md", SAMPLE)
        fails += check("get_rules: discovers STYLEGUIDE.md", len(get_rules("o/n", d)) == 3)

        # --- precedence: .prism/rules.md wins over STYLEGUIDE.md ---
        _write(d, ".prism/rules.md", "## Only\n- single prism rule\n")
        got = get_rules("o/n", d)
        fails += check(".prism/rules.md wins over STYLEGUIDE.md", got == ["Only: single prism rule"])

    with tempfile.TemporaryDirectory() as empty:
        # --- fallback: no rules file -> built-in static list ---
        builtin = get_rules("o/n", empty)
        fails += check("get_rules: falls back to built-in list", builtin == list(_BUILTIN_RULES))
        fails += check("built-in list is the full default ruleset (30-40 rules)", 30 <= len(_BUILTIN_RULES) <= 40)

        # --- override: explicit rules_file wins over discovery ---
        override = _write(empty, "custom.md", "- overridden rule\n")
        got = get_rules("o/n", empty, rules_file=override)
        fails += check("rules_file override is used", got == ["overridden rule"])

        # --- override that parses to nothing -> falls through to built-in ---
        blank = _write(empty, "blank.md", "no bullets here\n")
        fails += check(
            "empty override falls back to built-in",
            get_rules("o/n", empty, rules_file=blank) == list(_BUILTIN_RULES),
        )

    return fails
