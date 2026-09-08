"""Precision / recall / F1 for impact detection.

A "flag" is a (file, symbol) pair. Predicted flags come from the analyzer;
expected flags come from a case's hand-verified ground truth. These functions
are pure (no dependency on the app) so they're trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

Flag = Tuple[str, str]  # (file, symbol)


def _ratio(numerator: int, denominator: int) -> float:
    # 0/0 -> 1.0 (vacuously perfect); raw TP/FP/FN counts are always reported
    # alongside, so this convention never hides what actually happened.
    return 1.0 if denominator == 0 else numerator / denominator


def _f1(precision: float, recall: float) -> float:
    return 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)


@dataclass
class CaseMetrics:
    name: str
    tp: List[Flag]
    fp: List[Flag]
    fn: List[Flag]

    @property
    def precision(self) -> float:
        return _ratio(len(self.tp), len(self.tp) + len(self.fp))

    @property
    def recall(self) -> float:
        return _ratio(len(self.tp), len(self.tp) + len(self.fn))

    @property
    def f1(self) -> float:
        return _f1(self.precision, self.recall)


def score_case(name: str, predicted: Set[Flag], expected: Set[Flag]) -> CaseMetrics:
    return CaseMetrics(
        name=name,
        tp=sorted(predicted & expected),
        fp=sorted(predicted - expected),
        fn=sorted(expected - predicted),
    )


def micro_average(cases: List[CaseMetrics]) -> Dict[str, float]:
    """Pool TP/FP/FN across cases, then compute the rates (weights by flag count)."""
    tp = sum(len(c.tp) for c in cases)
    fp = sum(len(c.fp) for c in cases)
    fn = sum(len(c.fn) for c in cases)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def macro_average(cases: List[CaseMetrics]) -> Dict[str, float]:
    """Average the per-case rates (weights every case equally)."""
    n = len(cases)
    if n == 0:
        return {"precision": 1.0, "recall": 1.0, "f1": 0.0}
    precision = sum(c.precision for c in cases) / n
    recall = sum(c.recall for c in cases) / n
    return {"precision": precision, "recall": recall, "f1": sum(c.f1 for c in cases) / n}
