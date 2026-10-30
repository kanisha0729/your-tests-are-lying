"""Execution engine: apply a mutation, run pytest in an isolated copy,
and verify generated tests. No model output is ever executed as a shell
command. Mutations are exact text patches against the known module only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_DIR = Path(__file__).parent / "sample_project"
TARGET_FILE = "pricing.py"
TIMEOUT_S = 20


@dataclass
class Mutation:
    id: str
    title: str          # e.g. "Off-by-one on bulk threshold"
    original: str       # exact snippet from the source
    mutated: str        # replacement snippet
    why_realistic: str = ""
    status: str = "pending"   # pending | invalid | killed | survived
    detail: str = ""


@dataclass
class TestResult:
    passed: bool
    output: str
    timed_out: bool = False


@dataclass
class Verification:
    mutation_id: str
    test_code: str
    passes_on_original: bool = False
    fails_on_mutant: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.passes_on_original and self.fails_on_mutant


def read_source() -> str:
    return (SAMPLE_DIR / TARGET_FILE).read_text(encoding="utf-8")


def read_tests() -> str:
    return (SAMPLE_DIR / "tests" / "test_pricing.py").read_text(encoding="utf-8")


def validate_mutation(m: Mutation, source: str) -> str | None:
    """Return an error message, or None if the patch is safe to apply."""
    if not m.original or m.original == m.mutated:
        return "empty or no-op patch"
    count = source.count(m.original)
    if count != 1:
        return f"original snippet found {count} times (must be exactly 1)"
    mutated_src = source.replace(m.original, m.mutated, 1)
    try:
        compile(mutated_src, TARGET_FILE, "exec")
    except SyntaxError as e:
        return f"mutant does not compile: {e.msg}"
    banned = ("import os", "import subprocess", "open(", "__import__", "eval(", "exec(")
    if any(b in m.mutated for b in banned):
        return "patch contains disallowed code"
    return None


def _run_pytest(workdir: Path, test_path: str = "tests") -> TestResult:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test_path],
            cwd=workdir, capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return TestResult(passed=False, output="timed out", timed_out=True)
    out = (proc.stdout + proc.stderr)[-3000:]
    return TestResult(passed=proc.returncode == 0, output=out)


def _make_copy(source_override: str | None = None,
               extra_test: str | None = None) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ytl_"))
    shutil.copytree(SAMPLE_DIR, tmp, dirs_exist_ok=True)
    (tmp / "conftest.py").write_text(
        "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n",
        encoding="utf-8")
    if source_override is not None:
        (tmp / TARGET_FILE).write_text(source_override, encoding="utf-8")
    if extra_test is not None:
        (tmp / "tests" / "test_generated.py").write_text(extra_test, encoding="utf-8")
    return tmp


def run_suite(source_override: str | None = None,
              extra_test: str | None = None,
              only_generated: bool = False) -> TestResult:
    tmp = _make_copy(source_override, extra_test)
    try:
        target = "tests/test_generated.py" if only_generated else "tests"
        return _run_pytest(tmp, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def evaluate_mutation(m: Mutation, source: str | None = None) -> Mutation:
    """Run the existing suite against one mutant. Killed = suite fails."""
    source = source or read_source()
    err = validate_mutation(m, source)
    if err:
        m.status, m.detail = "invalid", err
        return m
    mutant_src = source.replace(m.original, m.mutated, 1)
    result = run_suite(source_override=mutant_src)
    if result.timed_out:
        m.status, m.detail = "killed", "timed out (counts as caught)"
    elif result.passed:
        m.status, m.detail = "survived", "all existing tests passed on buggy code"
    else:
        m.status, m.detail = "killed", "existing tests caught it"
    return m


def verify_generated_test(m: Mutation, test_code: str,
                          source: str | None = None) -> Verification:
    """A generated test only counts if it PASSES on the original code and
    FAILS on the mutant. Anything else is rejected."""
    source = source or read_source()
    v = Verification(mutation_id=m.id, test_code=test_code)
    try:
        compile(test_code, "test_generated.py", "exec")
    except SyntaxError as e:
        v.note = f"generated test does not compile: {e.msg}"
        return v
    on_original = run_suite(extra_test=test_code, only_generated=True)
    v.passes_on_original = on_original.passed
    if not on_original.passed:
        v.note = "rejected: test fails on correct code (the test itself is wrong)"
        v.extra["original_output"] = on_original.output
        return v
    mutant_src = source.replace(m.original, m.mutated, 1)
    on_mutant = run_suite(source_override=mutant_src, extra_test=test_code,
                          only_generated=True)
    v.fails_on_mutant = not on_mutant.passed
    v.note = ("verified: passes on original, fails on mutant" if v.fails_on_mutant
              else "rejected: test does not detect the bug")
    return v
