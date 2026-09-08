"""Checks for explain_impact prompt assembly.

The key invariant: with no rules the prompt is byte-identical to the rule-free
behaviour, and rules only *append* (never rewrite) the system and user prompts.
`generate` is monkeypatched so no LLM call is made.
"""
import app.llm_service as llm
from tests import check

_captured: dict = {}


def _fake_generate(system_prompt, user_prompt):
    _captured["system"] = system_prompt
    _captured["user"] = user_prompt
    return "  canned explanation  "


def _call(rules):
    llm.generate = _fake_generate  # patch the name bound in llm_service
    out = llm.explain_impact(
        changed_symbol="foo",
        before_code="def foo(): pass",
        after_code="def foo(): return 1",
        impacted_file="bar.py",
        call_site_code="x = foo()",
        rules=rules,
    )
    return _captured["system"], _captured["user"], out


def run() -> int:
    fails = 0

    base_sys, base_user, base_out = _call(None)
    fails += check("no-rules: user prompt has no rules section", "Internal engineering rules" not in base_user)
    fails += check("no-rules: system prompt has no rules guardrail", "never invent, infer, or generalize" not in base_sys)
    fails += check("output is stripped", base_out == "canned explanation")

    r_sys, r_user, _ = _call([
        "Error handling: never swallow exceptions.",
        "API design: keep return types stable.",
    ])
    fails += check("rules: user prompt only appends (base unchanged)", r_user.startswith(base_user))
    fails += check("rules: system prompt only appends (base unchanged)", r_sys.startswith(base_sys))
    fails += check("rules: section header present", "Internal engineering rules to enforce:" in r_user)
    fails += check("rules: numbered 1", "1. Error handling: never swallow exceptions." in r_user)
    fails += check("rules: numbered 2", "2. API design: keep return types stable." in r_user)
    fails += check("rules: user prompt asks to cite the rule number", "cite the rule number" in r_user)
    fails += check("rules: system guardrail added", "never invent, infer, or generalize" in r_sys)

    empty_sys, empty_user, _ = _call([])
    fails += check("empty list behaves like no rules (user)", empty_user == base_user)
    fails += check("empty list behaves like no rules (system)", empty_sys == base_sys)

    return fails
