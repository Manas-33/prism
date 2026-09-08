from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .models import AnalysisResult, ProjectConfig, Task, iter_tags


@dataclass(frozen=True)
class RiskPolicy:
    high_risk_threshold: float = 5.0
    max_warning_threshold: int = 3


class Analyzer:
    def __init__(self, config: ProjectConfig, policy: RiskPolicy | None = None) -> None:
        self._config = config
        self._policy = policy or RiskPolicy()

    def analyze(self, tasks: Iterable[Task]) -> AnalysisResult:
        task_list = list(tasks)
        if not task_list:
            return AnalysisResult.empty().with_warning("No tasks provided")

        total_points = sum(task.points for task in task_list)
        tag_counts = dict(Counter(iter_tags(task_list)))
        risk_score = self._score_risk(tag_counts)

        warnings: list[str] = []
        if len(task_list) > self._config.max_items:
            warnings.append("Task count exceeds max_items")
        if risk_score > self._policy.high_risk_threshold:
            warnings.append("Risk score is high")

        if len(warnings) > self._policy.max_warning_threshold:
            warnings = warnings[: self._policy.max_warning_threshold]

        return AnalysisResult(
            total_points=total_points,
            tag_counts=tag_counts,
            risk_score=risk_score,
            warnings=tuple(warnings),
            metadata={"task_count": str(len(task_list))},
        )

    def _score_risk(self, tag_counts: dict[str, int]) -> float:
        weights = self._config.risk_weights
        default_weight = self._config.default_weight
        return sum(count * weights.get(tag, default_weight) for tag, count in tag_counts.items())
