"""Aggregate runner: `python -m tests`."""
from tests import test_rules, test_prompt


def main() -> int:
    failures = 0
    for mod in (test_rules, test_prompt):
        print(f"== {mod.__name__} ==")
        failures += mod.run()
        print()
    print("OK — all checks passed" if not failures else f"{failures} check(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
