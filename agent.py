"""Nemotron agents on Nebius Token Factory.

Two jobs:
  1. plant_bugs()  - propose realistic, human-style bugs as exact text patches
  2. write_test()  - write one pytest test that catches a specific surviving bug

Every model output is parsed as JSON and validated by engine.py before use.
Nothing the model writes is run as a command.
"""
from __future__ import annotations

import json
import os
import re

from openai import OpenAI

from engine import Mutation

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
# Check exact model IDs on tokenfactory.nebius.com/models and override via env if needed.
MUTATOR_MODEL = os.environ.get("MUTATOR_MODEL", "nvidia/nemotron-3-super-120b-a12b")
TESTWRITER_MODEL = os.environ.get("TESTWRITER_MODEL", "nvidia/nemotron-3-super-120b-a12b")


def _client() -> OpenAI:
    key = os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise RuntimeError("Set NEBIUS_API_KEY (from tokenfactory.nebius.com).")
    return OpenAI(base_url=BASE_URL, api_key=key)


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model reply (handles ```json fences
    and reasoning text before the answer)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON in model reply")
    return json.JSONDecoder().raw_decode(text[start:])[0]


def _chat(model: str, system: str, user: str, temperature: float = 0.4) -> str:
    resp = _client().chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=4000,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


MUTATOR_SYSTEM = """You are a senior engineer who knows how real bugs happen.
Given a Python module and its tests, plant realistic bugs: the kind a tired
developer actually commits (off-by-one, flipped boolean, wrong boundary,
sign error, wrong variable, missing guard). NOT random syntax garbage.

Prefer bugs you believe the EXISTING tests will miss: study the tests and
target behaviour they do not pin down.

Rules:
- Each bug is an exact text patch: "original" must be copied character-for-character
  from the source and appear exactly once. Keep it short (one line or less).
- "mutated" must still be valid Python.
- No imports, no file or network access, no eval/exec.

Reply with ONLY a JSON array, no prose:
[{"title": "...", "original": "...", "mutated": "...",
  "why_realistic": "one sentence: how a developer would plausibly write this"}]"""


def plant_bugs(source: str, tests: str, n: int = 6) -> list[Mutation]:
    user = (f"Plant {n} distinct realistic bugs.\n\n### pricing.py\n```python\n{source}\n```"
            f"\n\n### tests/test_pricing.py\n```python\n{tests}\n```")
    raw = _chat(MUTATOR_MODEL, MUTATOR_SYSTEM, user, temperature=0.6)
    items = _extract_json(raw)
    out = []
    for i, it in enumerate(items[:n], 1):
        out.append(Mutation(
            id=f"m{i}",
            title=str(it.get("title", f"Bug {i}"))[:80],
            original=str(it.get("original", "")),
            mutated=str(it.get("mutated", "")),
            why_realistic=str(it.get("why_realistic", ""))[:200],
        ))
    return out


TESTWRITER_SYSTEM = """You write precise pytest regression tests.
You are given correct code, the existing tests, and ONE bug that the existing
tests failed to catch. Write ONE small test file that:
- PASSES on the correct code
- FAILS on the buggy code
Target the exact boundary or behaviour the bug changes. Use concrete values
and compute expected results from the CORRECT code, carefully.
Import with: from pricing import ...
No network, files, randomness, or sleeps.

Reply with ONLY a JSON object:
{"test_code": "<full python file>", "reasoning": "one sentence on which input exposes the bug"}"""


def write_test(source: str, tests: str, m: Mutation, previous_error: str = "") -> tuple[str, str]:
    user = (f"### Correct pricing.py\n```python\n{source}\n```\n\n"
            f"### Existing tests\n```python\n{tests}\n```\n\n"
            f"### Surviving bug: {m.title}\nChanged `{m.original}` to `{m.mutated}`")
    if previous_error:
        user += f"\n\nYour previous attempt was rejected: {previous_error}. Fix it."
    raw = _chat(TESTWRITER_MODEL, TESTWRITER_SYSTEM, user, temperature=0.2)
    obj = _extract_json(raw)
    return str(obj.get("test_code", "")), str(obj.get("reasoning", ""))
