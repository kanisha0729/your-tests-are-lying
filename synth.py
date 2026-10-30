"""Writes missing tests for surviving mutants, then PROVES each one.

A generated test is accepted only if it passes on the original code and
fails on the mutant. Mutants for which no distinguishing input exists are
reported as likely equivalent (the change does not alter behaviour).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from runner import Project, run_tests

WORKER = Path(__file__).parent / "synth_worker.py"
SEARCH_TIMEOUT_S = 20


@dataclass
class Synthesis:
    mutant_id: str
    status: str            # verified | rejected | equivalent | failed
    test_code: str = ""
    function: str = ""
    input: str = ""
    expected: str = ""
    mutant_gives: str = ""
    note: str = ""
    source: str = "search"  # search | ai


def search_test(project: Project, mutant) -> dict:
    job = {"module_name": project.module_name, "original_source": project.source,
           "mutant_source": mutant.mutant_source, "mutant_id": mutant.id}
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    try:
        proc = subprocess.run([sys.executable, str(WORKER), path], capture_output=True,
                              text=True, timeout=SEARCH_TIMEOUT_S)
        lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
        return json.loads(lines[-1]) if lines else {"found": False, "error": proc.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {"found": False, "error": "search timed out"}
    finally:
        os.unlink(path)


def test_file(project: Project, test_functions: list[str]) -> str:
    """Wrap test functions into a runnable file."""
    return (f"import pytest\n\nfrom {project.module_name} import *  # noqa: F401,F403\n\n\n"
            + "\n\n".join(test_functions))


def verify(project: Project, mutant, test_function: str) -> tuple[bool, str]:
    code = test_file(project, [test_function])
    try:
        compile(code, "test_generated.py", "exec")
    except SyntaxError as e:
        return False, f"generated test does not compile: {e.msg}"
    extra = {"test_generated.py": code}
    on_original = run_tests(project, extra_tests=extra, include_original_tests=False)
    if not on_original.passed:
        return False, "rejected: the test fails on the correct code"
    on_mutant = run_tests(project, source=mutant.mutant_source, extra_tests=extra,
                          include_original_tests=False)
    if on_mutant.passed:
        return False, "rejected: the test does not detect the bug"
    return True, "verified: passes on the correct code, fails on the bug"


def synthesize(project: Project, mutant) -> Synthesis:
    found = search_test(project, mutant)
    if not found.get("found"):
        if found.get("error"):
            return Synthesis(mutant.id, "failed", note=f"search failed: {found['error']}")
        return Synthesis(mutant.id, "equivalent",
                         note=f"no input out of {found.get('tried', 0)} tried makes the "
                              "buggy code behave differently, so this mutant is likely equivalent")
    ok, note = verify(project, mutant, found["test"])
    return Synthesis(mutant.id, "verified" if ok else "rejected", test_code=found["test"],
                     function=found["function"], input=found["input"],
                     expected=str(found["expected"]), mutant_gives=str(found["mutant_gives"]),
                     note=note)
