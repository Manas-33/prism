from __future__ import annotations

import argparse
import sys

from .formatter import format_report
from .services import Analyzer
from .tasks import build_tasks
from .utils import load_config


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Demo analysis CLI")
    parser.add_argument("--name", default="Demo Project")
    parser.add_argument("--config", help="Path to JSON config")
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        help="Task item in the format title:points:tag1,tag2",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    config = load_config(args.config, default_name=args.name)

    tasks = build_tasks(args.item)
    result = Analyzer(config).analyze(tasks)
    report = format_report(config, result)

    print(report)
    return 0
