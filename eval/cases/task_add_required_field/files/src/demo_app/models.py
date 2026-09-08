from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    max_items: int
    risk_weights: dict[str, float]
    default_weight: float = 1.0


@dataclass(frozen=True)
class Task:
    title: str
    points: int
    tags: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class AnalysisResult:
    total_points: int
    tag_counts: dict[str, int]
    risk_score: float
    warnings: tuple[str, ...]
    metadata: Mapping[str, str] | None = None

    @classmethod
    def empty(cls) -> AnalysisResult:
        return cls(total_points=0, tag_counts={}, risk_score=0.0, warnings=())

    def with_warning(self, message: str) -> AnalysisResult:
        return AnalysisResult(
            total_points=self.total_points,
            tag_counts=dict(self.tag_counts),
            risk_score=self.risk_score,
            warnings=(*self.warnings, message),
        )


def iter_tags(tasks: Iterable[Task]) -> Iterable[str]:
    for task in tasks:
        yield from task.tags
