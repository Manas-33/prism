from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ProjectConfig, ValidationError


def slugify(value: str, separator: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else separator for ch in value)
    doubled = separator * 2
    while doubled in cleaned:
        cleaned = cleaned.replace(doubled, separator)
    return cleaned.strip(separator)


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _parse_risk_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValidationError("risk_weights must be a mapping")
    weights: dict[str, float] = {}
    for key, value in raw.items():
        try:
            weights[str(key)] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid weight for {key!r}") from exc
    return weights


def load_config(path: str | None, *, default_name: str) -> ProjectConfig:
    raw: dict[str, Any] = {}
    env_value = os.getenv("DEMO_APP_CONFIG")
    if env_value:
        raw = json.loads(env_value)
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))

    name = str(raw.get("name") or default_name)
    max_items = int(raw.get("max_items") or 10)
    risk_weights = _parse_risk_weights(raw.get("risk_weights") or {"default": 1.0})
    default_weight = float(raw.get("default_weight") or risk_weights.get("default", 1.0))

    if max_items < 1:
        raise ValidationError("max_items must be >= 1")

    max_items = clamp(max_items, 1, 100)
    return ProjectConfig(
        name=name,
        max_items=max_items,
        risk_weights=risk_weights,
        default_weight=default_weight,
    )
