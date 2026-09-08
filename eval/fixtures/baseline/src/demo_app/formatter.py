from __future__ import annotations

from .models import AnalysisResult, ProjectConfig
from .utils import slugify


def format_report(config: ProjectConfig, result: AnalysisResult) -> str:
    lines = [
        f"Project: {config.name}",
        f"Slug: {slugify(config.name)}",
        f"Total points: {result.total_points}",
        f"Risk score: {result.risk_score:.2f}",
    ]

    if result.metadata:
        lines.append("Metadata:")
        for key, value in sorted(result.metadata.items()):
            lines.append(f"  - {key}: {value}")

    if result.tag_counts:
        lines.append("Tag counts:")
        for tag, count in sorted(result.tag_counts.items()):
            lines.append(f"  - {tag}: {count}")

    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")

    return "\n".join(lines)
