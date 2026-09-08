"""Load annotated cases, run the analyzer, score precision/recall, report.

Each case is materialized as a throwaway two-commit git repo:
  1. copy the shared baseline tree, commit  -> base_sha
  2. overlay the case's changed file(s), commit -> head_sha
then `app.analysis.analyze_impacts` runs on (repo, base_sha, head_sha) with
explain=False / use_cache=False (graph-only, no secrets, no services).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from app.analysis import analyze_impacts
from eval.metrics import CaseMetrics, Flag, macro_average, micro_average, score_case

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_DIR = os.path.join(EVAL_DIR, "fixtures", "baseline")
CASES_DIR = os.path.join(EVAL_DIR, "cases")

# Confidence thresholds matching app.confidence.confidence_label boundaries.
THRESHOLDS: List[Tuple[float, str]] = [(0.0, "all"), (0.4, "medium+"), (0.75, "high")]


# ---------------------------------------------------------------------------
# Fixture repo construction
# ---------------------------------------------------------------------------
def _git(repo_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        check=True, capture_output=True, text=True,
    )


def _commit(repo_dir: str, message: str) -> None:
    _git(
        repo_dir,
        "-c", "user.name=prism-eval",
        "-c", "user.email=eval@prism.local",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message,
    )


def _rev_parse(repo_dir: str) -> str:
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _overlay(src_tree: str, dst_repo: str) -> None:
    for root, _, files in os.walk(src_tree):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, src_tree)
            dst = os.path.join(dst_repo, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # Replace with a fresh file (new inode, mtime=now) and DON'T preserve
            # the source mtime: a same-size content change (e.g. a 10-char symbol
            # rename) with a preserved/older mtime looks "unchanged" to git's
            # stat cache on a fresh checkout, so `git add` never re-hashes it.
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copyfile(src, dst)


def build_case_repo(case_dir: str, workspace: str) -> Tuple[str, str, str]:
    repo_dir = os.path.join(workspace, "repo")
    shutil.copytree(BASELINE_DIR, repo_dir)

    _git(repo_dir, "init", "-q")
    _git(repo_dir, "add", "-A")
    _commit(repo_dir, "baseline")
    base_sha = _rev_parse(repo_dir)

    files_dir = os.path.join(case_dir, "files")
    if os.path.isdir(files_dir):
        _overlay(files_dir, repo_dir)

    _git(repo_dir, "add", "-A")
    # `git diff --cached --quiet` exits 0 when nothing is staged (no change).
    if subprocess.run(["git", "-C", repo_dir, "diff", "--cached", "--quiet"]).returncode == 0:
        raise ValueError("case overlay makes no change to the baseline")
    _commit(repo_dir, "change")
    head_sha = _rev_parse(repo_dir)
    return repo_dir, base_sha, head_sha


# ---------------------------------------------------------------------------
# Case loading + running
# ---------------------------------------------------------------------------
def discover_cases(cases_dir: str, only: Optional[str] = None) -> List[str]:
    names = []
    for entry in sorted(os.listdir(cases_dir)):
        case_dir = os.path.join(cases_dir, entry)
        if os.path.isfile(os.path.join(case_dir, "case.json")):
            if only is None or entry == only:
                names.append(entry)
    return names


def load_case(case_dir: str) -> dict:
    with open(os.path.join(case_dir, "case.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def expected_flags(case: dict) -> Set[Flag]:
    return {(e["file"], e["symbol"]) for e in case["expected_impacts"]}


def expected_kinds(case: dict) -> Dict[Flag, str]:
    return {(e["file"], e["symbol"]): e.get("kind", "") for e in case["expected_impacts"]}


def flags_at(impacts: List[dict], min_score: float) -> Set[Flag]:
    return {
        (imp["file"].replace(os.sep, "/"), imp["symbol"])
        for imp in impacts
        if imp["score"] >= min_score
    }


def run_case(name: str) -> dict:
    """Materialize, analyze, and return a record with the raw impacts (or an error)."""
    case_dir = os.path.join(CASES_DIR, name)
    case = load_case(case_dir)
    record: dict = {
        "name": name,
        "description": case.get("description", ""),
        "expected": expected_flags(case),
        "kinds": expected_kinds(case),
    }
    try:
        with tempfile.TemporaryDirectory(prefix=f"prism-eval-{name}-") as workspace:
            repo_dir, base_sha, head_sha = build_case_repo(case_dir, workspace)
            result = analyze_impacts(
                repo_dir, name, base_sha, head_sha, explain=False, use_cache=False
            )
            record["base_sha"] = base_sha
            record["head_sha"] = head_sha
            record["impacts"] = [
                {"file": i["file"].replace(os.sep, "/"), "symbol": i["symbol"],
                 "score": i["score"], "label": i["label"]}
                for i in result.impacts
            ]
    except Exception as e:  # noqa: BLE001 - a broken case shouldn't sink the run
        record["error"] = f"{type(e).__name__}: {e}"
    return record


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _metrics_for(records: List[dict], min_score: float) -> List[CaseMetrics]:
    out = []
    for rec in records:
        if "error" in rec:
            continue
        predicted = flags_at(rec["impacts"], min_score)
        out.append(score_case(rec["name"], predicted, rec["expected"]))
    return out


def build_report(records: List[dict], min_score: float) -> dict:
    ok = [r for r in records if "error" not in r]
    errored = [r for r in records if "error" in r]

    case_metrics = _metrics_for(records, min_score)
    by_name = {cm.name: cm for cm in case_metrics}

    sweep = []
    for t, label in THRESHOLDS:
        sweep.append({
            "min_score": t,
            "label": label,
            "micro": micro_average(_metrics_for(records, t)),
        })

    cases_out = []
    for rec in ok:
        cm = by_name[rec["name"]]
        cases_out.append({
            "name": rec["name"],
            "base_sha": rec.get("base_sha"),
            "head_sha": rec.get("head_sha"),
            "precision": cm.precision,
            "recall": cm.recall,
            "f1": cm.f1,
            "tp": [list(f) for f in cm.tp],
            "fp": [list(f) for f in cm.fp],
            "fn": [[f[0], f[1], rec["kinds"].get(f, "")] for f in cm.fn],
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_cases": len(records),
        "num_errored": len(errored),
        "min_score": min_score,
        "aggregate": {
            "micro": micro_average(case_metrics),
            "macro": macro_average(case_metrics),
        },
        "threshold_sweep": sweep,
        "cases": cases_out,
        "errors": [{"name": r["name"], "error": r["error"]} for r in errored],
    }


def format_human(report: dict) -> str:
    lines: List[str] = []
    lines.append(f"PRism impact-detection eval — {report['num_cases']} case(s), "
                 f"min_score={report['min_score']:.2f}")
    lines.append("")
    header = f"{'CASE':<34}{'P':>6}{'R':>7}{'F1':>7}{'TP':>4}{'FP':>4}{'FN':>4}"
    lines.append(header)
    lines.append("-" * len(header))
    for c in report["cases"]:
        lines.append(
            f"{c['name']:<34}{c['precision']:>6.2f}{c['recall']:>7.2f}{c['f1']:>7.2f}"
            f"{len(c['tp']):>4}{len(c['fp']):>4}{len(c['fn']):>4}"
        )
    micro = report["aggregate"]["micro"]
    macro = report["aggregate"]["macro"]
    lines.append("-" * len(header))
    lines.append(
        f"{'MICRO':<34}{micro['precision']:>6.2f}{micro['recall']:>7.2f}{micro['f1']:>7.2f}"
        f"{micro['tp']:>4}{micro['fp']:>4}{micro['fn']:>4}"
    )
    lines.append(
        f"{'MACRO':<34}{macro['precision']:>6.2f}{macro['recall']:>7.2f}{macro['f1']:>7.2f}"
    )

    lines.append("")
    lines.append("Confidence threshold sweep (micro):")
    for s in report["threshold_sweep"]:
        m = s["micro"]
        lines.append(
            f"  score>={s['min_score']:.2f} ({s['label']:<7})  "
            f"P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}  "
            f"(TP={m['tp']} FP={m['fp']} FN={m['fn']})"
        )

    fns = [(c["name"], f) for c in report["cases"] for f in c["fn"]]
    fps = [(c["name"], f) for c in report["cases"] for f in c["fp"]]
    if fns:
        lines.append("")
        lines.append("Missed impacts (false negatives):")
        for name, f in fns:
            kind = f" [{f[2]}]" if len(f) > 2 and f[2] else ""
            lines.append(f"  {name}: {f[0]}:{f[1]}{kind}")
    if fps:
        lines.append("")
        lines.append("Spurious impacts (false positives):")
        for name, f in fps:
            lines.append(f"  {name}: {f[0]}:{f[1]}")
    if report["num_errored"]:
        lines.append("")
        lines.append("Errored cases:")
        for e in report["errors"]:
            lines.append(f"  {e['name']}: {e['error']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="Precision/recall eval for PRism's impact detection.",
    )
    parser.add_argument("--case", default=None, help="Run a single case by name.")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help="Only count predicted impacts with score >= this (default 0.0 = all).")
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="Write the full JSON report to PATH.")
    parser.add_argument("--fail-under-precision", type=float, default=None,
                        help="Exit non-zero if micro precision is below this.")
    parser.add_argument("--fail-under-recall", type=float, default=None,
                        help="Exit non-zero if micro recall is below this.")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    names = discover_cases(CASES_DIR, only=args.case)
    if not names:
        print(f"No cases found in {CASES_DIR}"
              + (f" matching --case {args.case}" if args.case else ""), file=sys.stderr)
        return 2

    records = [run_case(name) for name in names]
    report = build_report(records, args.min_score)

    print(format_human(report))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nWrote JSON report to {args.json}", file=sys.stderr)

    micro = report["aggregate"]["micro"]
    exit_code = 0
    if report["num_errored"]:
        exit_code = 1
    if args.fail_under_precision is not None and micro["precision"] < args.fail_under_precision:
        print(f"FAIL: micro precision {micro['precision']:.2f} < {args.fail_under_precision:.2f}",
              file=sys.stderr)
        exit_code = 1
    if args.fail_under_recall is not None and micro["recall"] < args.fail_under_recall:
        print(f"FAIL: micro recall {micro['recall']:.2f} < {args.fail_under_recall:.2f}",
              file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
