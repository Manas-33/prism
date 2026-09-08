"""Precision/recall evaluation harness for PRism's impact detection.

Runs the analysis core (`app.analysis.analyze_impacts`) against annotated,
hermetic fixtures and scores predicted impacts against hand-verified ground
truth. Graph-only (no LLM, no Redis, no GitHub) — see `python -m eval --help`.
"""
