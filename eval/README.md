# Impact-detection eval (§5)

Precision/recall harness for PRism's core question: *given a change, which
downstream files does it flag, and are those the right ones?*

It runs the analysis core (`app.analysis.analyze_impacts`) against annotated,
hermetic fixtures — **graph-only** (`explain=False`, `use_cache=False`), so no
GitHub App key, no Redis, and no LLM/Gemini key are needed.

## Run it

```bash
uv run python -m eval                 # human summary for all cases
uv run python -m eval --json out.json # also write the full JSON report
uv run python -m eval --case slugify_signature_change   # one case
uv run python -m eval --min-score 0.4 # only count Medium+ confidence flags
```

CI gating (used in `.github/workflows/eval.yml`):

```bash
uv run python -m eval --fail-under-precision 1.0 --fail-under-recall 0.75
```

## Current baseline

| Metric | Value |
|---|---|
| Micro precision | **1.00** |
| Micro recall | **0.75** |
| Micro F1 | **0.86** |

All misses are **attribute-access** dependencies (e.g. `self._config.risk_weights`,
`result.tag_counts`). PRism resolves *call* edges, so a field renamed on a
dataclass is caught where the object is *constructed* but missed where its
attributes are merely *read*. Precision is 1.00: every flag is a real dependency.

## How a flag is scored

A **flag** is a `(file, symbol)` pair. Predicted flags come from
`analyze_impacts`; expected flags come from a case's `expected_impacts`.

- **True positive** — predicted ∩ expected
- **False positive** — predicted − expected (over-flagging → hurts precision)
- **False negative** — expected − predicted (missed impact → hurts recall)

Reported both **micro** (pool TP/FP/FN across cases) and **macro** (average the
per-case rates), plus a sweep over the confidence thresholds.

## Annotation format

Each case is a directory under `eval/cases/<name>/`:

```
cases/<name>/
  case.json                     # metadata + hand-verified ground truth
  files/                        # the file(s) this "PR" changes, overlaid on the baseline
    src/demo_app/<changed>.py
```

`case.json`:

```json
{
  "name": "slugify_signature_change",
  "description": "Why this change impacts the files below.",
  "changed_symbols": ["slugify"],
  "expected_impacts": [
    {"file": "src/demo_app/formatter.py", "symbol": "slugify", "kind": "call"}
  ]
}
```

`kind` is documentation only (`call` vs `attribute-access`); it is not used in
scoring but is printed next to false negatives so the *reason* for a miss is
visible.

The harness builds a throwaway two-commit git repo per case — baseline commit,
then the overlay commit — and analyzes `(repo, base_sha, head_sha)`.

## Adding a case

1. `mkdir -p eval/cases/<name>/files`
2. Copy the file(s) you change from `eval/fixtures/baseline/` into `files/`,
   mirroring the path, and make a **surgical** edit (touch one symbol so the
   diff isolates it).
3. Write `case.json` with the impacts that **truly** break — verified by reading
   the code, **not** by trusting PRism's output (that would make the eval
   circular).
4. `uv run python -m eval --case <name>`.

The baseline in `eval/fixtures/baseline/` is a snapshot of `demo_project`'s
`src/demo_app/` package.
