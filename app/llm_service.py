from app.api_service import generate

def explain_impact(
    *,
    changed_symbol: str,
    before_code: str,
    after_code: str,
    impacted_file: str,
    call_site_code: str,
    rules: list[str] | None = None,
) -> str:
    system_prompt = (
        "You are an AI code review assistant.\n"
        "You are NOT allowed to:\n"
        "- invent new dependencies\n"
        "- assume bugs without evidence\n"
        "- mention files or functions not provided\n\n"
        "You MUST:\n"
        "- explain potential impact conservatively\n"
        "- base reasoning only on the provided code\n"
        "- be concise (3–5 sentences max)\n"
    )

    user_prompt = f"""A function named `{changed_symbol}` has changed in a pull request.

Changed function BEFORE:
```python
{before_code}
```

Changed function AFTER:
```python
{after_code}
```
This function is called in the following file:
`{impacted_file}`

Call site:
```python
{call_site_code}
```

Explain:

why this change might affect `{impacted_file}`

what the developer should double-check

do NOT assume this is a bug unless it clearly is"""

    # Internal engineering rules (Stage A). Rendered only when rules are
    # present, so the empty/None path leaves both prompts byte-identical to the
    # generic behaviour — that is the automatic fallback for repos with no rules.
    if rules:
        system_prompt += (
            "\nWhen internal engineering rules are provided, you MUST:\n"
            "- only enforce rules explicitly listed — never invent, infer, or "
            "generalize rules that were not provided\n"
            "- judge each rule using only the before/after code and the call site\n"
            "- cite the rule number when you flag a violation\n"
        )
        numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))
        user_prompt += (
            "\n\nInternal engineering rules to enforce:\n"
            f"{numbered}\n\n"
            "Check the AFTER code and the call site against these rules. If one "
            "is violated, say so explicitly and cite the rule number. Do not "
            "enforce any rule not listed above."
        )

    response = generate(
    system_prompt=system_prompt,
    user_prompt=user_prompt,
    )

    return response.strip()
    
