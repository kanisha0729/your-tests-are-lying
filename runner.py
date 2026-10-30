"""Runs pytest against a project in a throwaway folder, with a timeout.

The original files are never modified. Each run gets its own temp copy, so
mutants can be tested in parallel.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_S = 20
_ENV = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}


@dataclass
class Project:
    name: str            # display name
    module_name: str     # e.g. "pricing" (file pricing.py)
    source: str
    tests: str           # contents of the existing test file

    @property
    def test_filename(self) -> str:
        return f"test_{self.module_name}.py"


@dataclass
class TestResult:
    passed: bool
    output: str
    timed_out: bool = False


def load_sample(folder: Path) -> Project:
    module_file = next(p for p in folder.glob("*.py") if not p.name.startswith("test_"))
    test_file = folder / f"test_{module_file.stem}.py"
    return Project(name=folder.name, module_name=module_file.stem,
                   source=module_file.read_text(encoding="utf-8"),
                   tests=test_file.read_text(encoding="utf-8"))


def _workspace(project: Project, source: str | None,
               extra_tests: dict[str, str] | None, include_original_tests: bool) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ytl_"))
    (tmp / f"{project.module_name}.py").write_text(source or project.source, encoding="utf-8")
    tests_dir = tmp / "tests"
    tests_dir.mkdir()
    if include_original_tests:
        (tests_dir / project.test_filename).write_text(project.tests, encoding="utf-8")
    for name, code in (extra_tests or {}).items():
        (tests_dir / name).write_text(code, encoding="utf-8")
    (tmp / "conftest.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n", encoding="utf-8")
    return tmp


def run_tests(project: Project, source: str | None = None,
              extra_tests: dict[str, str] | None = None,
              include_original_tests: bool = True) -> TestResult:
    tmp = _workspace(project, source, extra_tests, include_original_tests)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
            cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_S, env=_ENV)
        return TestResult(proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:])
    except subprocess.TimeoutExpired:
        return TestResult(False, "timed out", timed_out=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def line_coverage(project: Project) -> float | None:
    """Percent of the module's lines executed by its own tests (needs `coverage`)."""
    tmp = _workspace(project, None, None, True)
    try:
        run = subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--include={project.module_name}.py",
             "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_S * 2, env=_ENV)
        if "No module named coverage" in run.stderr:
            return None
        subprocess.run([sys.executable, "-m", "coverage", "json", "-o", "cov.json", "-q"],
                       cwd=tmp, capture_output=True, text=True, timeout=TIMEOUT_S, env=_ENV)
        data = json.loads((tmp / "cov.json").read_text(encoding="utf-8"))
        return round(data["totals"]["percent_covered"], 1)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def evaluate(project: Project, mutants: list, extra_tests: dict[str, str] | None = None,
             workers: int = 4, on_progress=None) -> list:
    """Run the test suite against every mutant. Suite fails = mutant killed."""
    def one(m):
        try:
            compile(m.mutant_source, f"{project.module_name}.py", "exec")
        except SyntaxError as e:
            m.status, m.detail = "invalid", f"does not compile: {e.msg}"
            return m
        r = run_tests(project, source=m.mutant_source, extra_tests=extra_tests)
        if r.timed_out:
            m.status, m.detail = "timeout", ("the tests did not finish in time (often an infinite "
                                             "loop); counted as detected, like PIT and mutmut do")
        elif r.passed:
            m.status, m.detail = "survived", "every test passed on the buggy code"
        else:
            m.status, m.detail = "killed", "a test failed, so the bug was caught"
        return m

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(one, mutants):
            done += 1
            if on_progress:
                on_progress(done, len(mutants))
    return mutants


def mutation_score(mutants: list) -> float | None:
    """detected / (detected + survived) x 100, where detected = killed + timeout.
    Invalid mutants (do not compile) are excluded from the denominator."""
    valid = [m for m in mutants if m.status in ("killed", "timeout", "survived")]
    if not valid:
        return None
    detected = sum(m.status in ("killed", "timeout") for m in valid)
    return round(100 * detected / len(valid), 1)
