"""
Offline entry point for PRism's analysis core.

    python -m app.cli owner/name <base_sha> <head_sha> [--local PATH] [--explain]

Runs the exact same analysis the webhook worker runs, but on a
(repo, base_sha, head_sha) triple instead of a live PR — no webhook, no comment
posting. This is the local dev loop, and the runner the eval harness (§5) and
OSS bug-hunt (C2) build on.
"""
import argparse
import json
import logging
import sys
import uuid

from dotenv import load_dotenv

from app.workspace import job_workspace
from app.pipeline import analyze_refs
from app.render import render_pr_comment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prism-analyze",
        description="Run PRism's impact analysis offline on (repo, base_sha, head_sha).",
    )
    parser.add_argument("repo", help="Repo identifier, e.g. 'owner/name' (cache key + public clone URL).")
    parser.add_argument("base_sha", help="Base commit SHA (the 'before' state).")
    parser.add_argument("head_sha", help="Head commit SHA (the 'after' state).")
    parser.add_argument(
        "--local", metavar="PATH", default=None,
        help="Analyze a local git repo at PATH instead of cloning from GitHub.",
    )
    parser.add_argument(
        "--explain", action="store_true",
        help="Generate LLM explanations per impact (requires GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--cache", action="store_true",
        help="Use the Redis graph cache (off by default for offline runs).",
    )
    parser.add_argument(
        "--format", choices=["json", "summary", "both"], default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show INFO logs on stderr.",
    )
    return parser


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    job_id = uuid.uuid4().hex
    with job_workspace(job_id) as workspace:
        result = analyze_refs(
            repo=args.repo,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            workspace=workspace,
            local_path=args.local,
            explain=args.explain,
            use_cache=args.cache,
        )

    if args.format in ("summary", "both"):
        print(render_pr_comment(result))
        if args.format == "both":
            print()
    if args.format in ("json", "both"):
        json.dump(result.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
