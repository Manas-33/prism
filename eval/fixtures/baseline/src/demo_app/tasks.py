from __future__ import annotations

from .models import Task


def build_tasks(raw_items: list[str]) -> list[Task]:
    tasks: list[Task] = []
    for item in raw_items:
        title, points, tags = _parse_item(item)
        if points < 0:
            points = 0
        tasks.append(Task(title=title, points=points, tags=tags))
    return tasks


def _parse_item(raw: str) -> tuple[str, int, tuple[str, ...]]:
    # Format: title:points:tag1,tag2
    parts = raw.split(":", maxsplit=2)
    title = parts[0].strip() or "untitled"
    points = int(parts[1]) if len(parts) > 1 else 1
    tags = tuple(tag.strip() for tag in (parts[2].split(",") if len(parts) > 2 else []) if tag.strip())
    return title, points, tags
