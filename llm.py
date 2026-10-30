"""OPTIONAL AI mode. The project works fully without it.

Works with any OpenAI-compatible endpoint, for example:
  - Ollama on your own laptop (free, offline): base URL http://localhost:11434/v1
  - Any hosted provider that offers an OpenAI-compatible API

The AI only proposes text. Every patch and test it writes is validated and
run in a throwaway folder before it is trusted.
"""
from __future__ import annotations

import json
import re

from mutators import Mutant, short_diff

BANNED = ("import os", "import subprocess", "open(", "__import__", "eval(", "exec(")


class LLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # imported lazily: only needed in AI mode
        self.client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self.model = model

    def chat(self, system: str, user: str, temperature: float = 0.4) -> str:
        r = self.client.chat.completions.create(
            model=self.model, temperature=temperature, max_tokens=3000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return r.choices[0].message.content or ""


def extract_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON in model reply")
    return json.JSONDecoder().raw_decode(text[min(starts):])[0]


MUTATOR_SYSTEM = """You are a senior engineer who knows how real bugs happen.
Plant realistic bugs a tired developer would actually commit (off-by-one,
wrong boundary, wrong variable, missing guard, swapped arguments) that the
given tests probably miss. Each bug is an exact text patch: "original" must be
copied character-for-character from the source and appear exactly once.
No imports, file or network access, eval or exec.
Reply with ONLY a JSON array:
[{"title": "...", "original": "...", "mutated": "...", "why_realistic": "..."}]"""


def ai_mutants(llm: LLM, source: str, tests: str, n: int = 5) -> list[Mutant]:
    raw = llm.chat(MUTATOR_SYSTEM, f"Plant {n} bugs.\n\nSOURCE:\n{source}\n\nTESTS:\n{tests}", 0.6)
    out = []
    for item in extract_json(raw)[:n]:
        original, mutated = str(item.get("original", "")), str(item.get("mutated", ""))
        title = str(item.get("title", "AI bug"))[:80]
        m = Mutant(id=f"a{len(out) + 1:02d}", operator="AI", description=title, function=None,
                   line=0, mutant_source=source, diff="", origin="ai",
                   detail=str(item.get("why_realistic", ""))[:200])
        if not original or original == mutated or source.count(original) != 1:
            m.status, m.detail = "invalid", "patch does not match the source exactly once"
        elif any(b in mutated for b in BANNED):
            m.status, m.detail = "invalid", "patch contains disallowed code"
        else:
            m.mutant_source = source.replace(original, mutated, 1)
            m.diff = short_diff(source, m.mutant_source)
            m.line = source[: source.index(original)].count("\n") + 1
        out.append(m)
    return out


TEST_SYSTEM = """Write ONE pytest test function that PASSES on the correct code
and FAILS on the buggy code. Compute the expected value from the CORRECT code.
Name it test_ai_<something>. pytest and the module's functions are already
imported. Reply with ONLY JSON: {"test": "<the def ... function>"}"""


def ai_test(llm: LLM, source: str, mutant: Mutant) -> str:
    raw = llm.chat(TEST_SYSTEM, f"CORRECT CODE:\n{source}\n\nTHE BUG:\n{mutant.diff}", 0.2)
    return str(extract_json(raw).get("test", ""))
