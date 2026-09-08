"""Rendering of an AnalysisResult into the human-readable PR-comment summary.

Kept separate from the analysis core so callers that only need structured
impacts (eval harness, bug-hunt runner) never touch comment formatting.
"""
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from app.analysis import AnalysisResult


def _diff_counts(diff: str) -> Tuple[int, int, int]:
    files = set()
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            files.add(line[6:])
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return len(files), added, removed


def summarize_diff(diff: str) -> str:
    files, added, removed = _diff_counts(diff)
    return (
        "PR Change Summary:\n"
        f"Changed files: {files}\n"
        f"Lines added: {added}\n"
        f"Lines removed: {removed}\n"
    )


def render_pr_comment(result: "AnalysisResult") -> str:
    """Render a positioned PR-comment. The rules framing is shown only when
    explanations actually ran (explain=True), so graph-only runs stay honest."""
    files, added, removed = _diff_counts(result.diff)
    explained = any(r.get("explanation") for r in result.impacts)

    intro = ("Prism traced this repo's dependency graph beyond the diff to find "
             "what the change can break downstream")
    intro += (", and checked each impact against the repo's engineering rules."
              if explained else ".")

    lines = [
        "## 🔬 Prism blast-radius review",
        "",
        intro,
        "",
        f"**Changed:** {files} file(s), +{added} / -{removed}",
    ]

    if result.changed_symbols:
        lines.append("")
        lines.append("**Changed symbols:**")
        for kind, name in result.changed_symbols:
            lines.append(f"- `{kind}` `{name}`")

    if result.impacts:
        lines.append("")
        lines.append("**Blast radius (downstream impacts):**")
        for r in result.impacts:
            lines.append(
                f"- `{r['file']}` (symbol: `{r['symbol']}`, confidence: {r['label']})"
            )
            if r.get("explanation"):
                lines.append(f"  ↳ {r['explanation']}")
    else:
        lines.append("")
        lines.append("No downstream impacts detected for the changed symbols.")

    footer = "Prism reviews the blast radius of a pull request, not just the diff."
    if explained:
        footer += (" Add a <code>.prism/rules.md</code> to your repo to enforce "
                   "your own engineering rules.")
    lines += ["", "---", f"<sub>{footer}</sub>"]

    return "\n".join(lines) + "\n"
