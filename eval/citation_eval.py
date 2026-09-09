"""
End-to-end rule-citation eval over the 160-PR benchmark's downstream flags.

Measures the RAG feature the way a user experiences it: for every production
downstream flag from the 160-PR run (eval/c2_multi_flags.json), run the REAL
explanation path — per-impact rule retrieval (builtin scope) + the Gemini
prompt — and record which rules the LLM cites as violated. A blind labeling
pass then scores CITATION PRECISION: of the violations Prism cites, how many
are real?

Blinding protocol (same discipline as the precision eval):
  * The labeling sheet shows ONLY the code evidence and the cited rule text —
    never the LLM's explanation, the flag's confidence tier, or the score, so
    the labeler can't be steered by the tool's own reasoning.
  * The sheet order is shuffled with a fixed seed; the join key lives in a
    separate file.

Usage (from the repo root):
  python -m eval.citation_eval run --repos-dir PATH [--limit N]   # LLM calls; resumable
  python -m eval.citation_eval sheet [--sample 40] [--seed 7]     # build blind labeling sheet
  python -m eval.citation_eval score                              # after filling the sheet
"""
import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time

os.environ.setdefault("QDRANT_URL", ":memory:")

from dotenv import load_dotenv

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
FLAGS_PATH = os.path.join(EVAL_DIR, "c2_multi_flags.json")
RESULTS_PATH = os.path.join(EVAL_DIR, "citation_results.jsonl")
SHEET_CSV = os.path.join(EVAL_DIR, "citation_sheet.csv")
SHEET_MD = os.path.join(EVAL_DIR, "citation_evidence.md")
KEY_PATH = os.path.join(EVAL_DIR, "citation_key.json")

CITE_RE = re.compile(r"[Rr]ules?\s*#?\s*(\d+)")


def _git(repo_dir, *args):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True).stdout


def _flag_key(rec) -> str:
    return f"{rec['merge']}:{rec['file']}:{rec['symbol']}"


def _load_done() -> set:
    done = set()
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done.add(_flag_key(json.loads(line)))
    return done


def _explain_with_retry(explain_impact, kwargs, attempts=4):
    delay = 5
    for i in range(attempts):
        try:
            return explain_impact(**kwargs), None
        except Exception as e:
            if i == attempts - 1:
                return None, f"{type(e).__name__}: {e}"
            time.sleep(delay)
            delay = min(delay * 3, 90)


def cmd_run(args) -> int:
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        print("error: GEMINI_API_KEY is not set")
        return 2

    from app.analysis import analyze_impacts, _impact_query_text, RETRIEVAL_K
    from app.llm_service import explain_impact
    from app.rag import ensure_builtin_ingested
    from app.rules import get_rules

    ensure_builtin_ingested()

    flags = json.load(open(FLAGS_PATH, encoding="utf-8"))
    done = _load_done()
    # Group the flag pool by PR so each PR is analyzed once.
    prs: dict = {}
    for fl in flags:
        prs.setdefault((fl["repo"], fl["merge"], fl["base"], fl["head"]), []).append(fl)
    print(f"{len(flags)} flags across {len(prs)} PRs; {len(done)} already done", flush=True)

    out = open(RESULTS_PATH, "a", encoding="utf-8")
    n_prs = 0
    for (repo, merge, base, head), group in prs.items():
        todo = [fl for fl in group if _flag_key(fl) not in done]
        if not todo:
            continue
        if args.limit and n_prs >= args.limit:
            break
        n_prs += 1
        repo_dir = os.path.join(args.repos_dir, repo.split("/")[1])
        _git(repo_dir, "checkout", "-q", "--detach", head)
        try:
            res = analyze_impacts(repo_dir, repo, base, head, explain=False, use_cache=False)
        except Exception as e:
            print(f"  analysis failed for {repo} {merge[:10]}: {e}", flush=True)
            continue
        by_key = {(i["file"].replace(os.sep, "/"), i["symbol"]): i for i in res.impacts}
        for fl in todo:
            impact = by_key.get((fl["file"], fl["symbol"]))
            rec = {**fl}
            if impact is None:
                rec["error"] = "flag not reproduced by current analysis"
                out.write(json.dumps(rec) + "\n"); out.flush()
                continue
            rules = get_rules(repo, repo_dir, query_text=_impact_query_text(impact), k=RETRIEVAL_K)
            explanation, err = _explain_with_retry(explain_impact, dict(
                changed_symbol=impact["symbol"],
                before_code=impact["before_code"],
                after_code=impact["after_code"],
                impacted_file=impact["file"],
                call_site_code=impact["call_site_code"],
                rules=rules,
            ))
            if err:
                rec["error"] = err
            else:
                cited = sorted({int(m) for m in CITE_RE.findall(explanation or "")})
                rec.update({
                    "rules": rules,
                    "explanation": explanation,
                    "cited_rule_numbers": [n for n in cited if 1 <= n <= len(rules)],
                    "cited_rules": [rules[n - 1] for n in cited if 1 <= n <= len(rules)],
                    "before_code": impact["before_code"],
                    "after_code": impact["after_code"],
                    "call_site_code": impact["call_site_code"],
                })
            out.write(json.dumps(rec) + "\n"); out.flush()
            status = rec.get("error") or (f"cites {rec['cited_rule_numbers']}" if rec.get("cited_rule_numbers") else "no citation")
            print(f"  {repo} {merge[:8]} {fl['file']}::{fl['symbol']} -> {status}", flush=True)
            time.sleep(args.sleep)
    out.close()
    print("run complete (rerun to resume any failures)", flush=True)
    return 0


def _load_results():
    recs = []
    with open(RESULTS_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))
    return recs


def cmd_sheet(args) -> int:
    recs = [r for r in _load_results() if r.get("cited_rules")]
    # One labeling item per (flag, cited rule).
    items = []
    for r in recs:
        for rule in r["cited_rules"]:
            items.append({"key": _flag_key(r), "rule": rule, "rec": r})
    rng = random.Random(args.seed)
    rng.shuffle(items)
    items = items[: args.sample]

    with open(SHEET_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item", "verdict (y = rule genuinely violated / n = not violated)"])
        for i in range(len(items)):
            w.writerow([i + 1, ""])

    with open(SHEET_MD, "w", encoding="utf-8") as f:
        f.write("# Citation labeling sheet (BLIND)\n\n")
        f.write("For each item: does the AFTER code or the call site genuinely violate the cited rule?\n")
        f.write("Judge from the code only. Record y/n per item in citation_sheet.csv.\n")
        f.write("Do NOT look at citation_results.jsonl until the sheet is filled.\n\n")
        for i, it in enumerate(items, 1):
            r = it["rec"]
            f.write(f"---\n\n## Item {i}\n\n**Cited rule:** {it['rule']}\n\n")
            f.write(f"**BEFORE:**\n```python\n{r['before_code']}\n```\n\n")
            f.write(f"**AFTER:**\n```python\n{r['after_code']}\n```\n\n")
            f.write(f"**Call site ({r['file']}):**\n```python\n{r['call_site_code']}\n```\n\n")

    json.dump([{"item": i + 1, "key": it["key"], "rule": it["rule"]} for i, it in enumerate(items)],
              open(KEY_PATH, "w", encoding="utf-8"), indent=2)
    print(f"{len(items)} items -> {SHEET_MD} (evidence) + {SHEET_CSV} (verdicts) + key file")
    return 0


def cmd_score(_args) -> int:
    from eval.rag_eval import wilson
    verdicts = {}
    with open(SHEET_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row[list(row)[1]].strip().lower()
            if v in ("y", "n"):
                verdicts[int(row["item"])] = v == "y"
    if not verdicts:
        print("no verdicts filled in yet")
        return 2
    n = len(verdicts)
    tp = sum(verdicts.values())
    lo, hi = wilson(tp, n)
    recs = _load_results()
    explained = [r for r in recs if "error" not in r]
    cited = [r for r in explained if r.get("cited_rules")]
    print(f"labeled: {n}   citation precision: {tp}/{n} = {tp/n:.2%}   [Wilson 95%: {lo:.2f}, {hi:.2f}]")
    print(f"context: {len(explained)} flags explained, {len(cited)} cited >=1 rule ({len(cited)/len(explained):.1%} citation rate)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="citation-eval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--repos-dir", required=True, help="Directory holding the requests/flask/click/werkzeug clones.")
    p_run.add_argument("--limit", type=int, default=0, help="Max PRs this invocation (0 = all).")
    p_run.add_argument("--sleep", type=float, default=0.5, help="Pause between LLM calls.")
    p_sheet = sub.add_parser("sheet")
    p_sheet.add_argument("--sample", type=int, default=40)
    p_sheet.add_argument("--seed", type=int, default=7)
    sub.add_parser("score")
    args = parser.parse_args(argv)
    return {"run": cmd_run, "sheet": cmd_sheet, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
