"""Rendering of an AnalysisResult into the human-readable PR-comment summary.

Kept separate from the analysis core so callers that only need structured
impacts (eval harness, bug-hunt runner) never touch comment formatting.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.analysis import AnalysisResult


def summarize_diff(diff: str) -> str:
    files = set()
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            filename = line[6:] if line.startswith("+++ b/") else line[6:]
            files.add(filename)
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return (
        "PR Change Summary:\n"
        f"Changed files: {len(files)}\n"
        f"Lines added: {added}\n"
        f"Lines removed: {removed}\n"
    )


def render_pr_comment(result: "AnalysisResult") -> str:
    summary = summarize_diff(result.diff)

    if result.changed_symbols:
        summary += "\nChanged symbols:\n"
        for kind, name in result.changed_symbols:
            summary += f"- {kind}: {name}\n"

    if result.impacts:
        summary += "\nImpacted files:\n"
        for r in result.impacts:
            summary += (
                f"- {r['file']} "
                f"(symbol: {r['symbol']}, confidence: {r['label']})\n"
            )
            if r.get("explanation"):
                summary += f"  ↳ {r['explanation']}\n"

    return summary
