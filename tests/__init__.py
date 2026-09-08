"""Zero-dependency unit checks for PRism (run: `python -m tests`).

Follows the repo's runnable-module idiom (cf. `python -m eval`) instead of
adding a pytest dependency. Each test module exposes `run() -> int` returning
the number of failed checks; `python -m tests` aggregates them.
"""


def check(name: str, cond: bool) -> int:
    """Print a PASS/FAIL line and return 0 on pass, 1 on fail."""
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    return 0 if cond else 1
