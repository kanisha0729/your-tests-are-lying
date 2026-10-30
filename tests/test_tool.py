"""Tests for the tool itself. Run with:  python -m pytest"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import runner  # noqa: E402
from mutators import Mutant, generate_mutants  # noqa: E402
from runner import Project, evaluate, load_sample, mutation_score, run_tests  # noqa: E402
from synth import synthesize  # noqa: E402

PRICING = load_sample(ROOT / "samples" / "pricing")


# ---------- mutation engine ----------

def test_every_mutant_compiles_and_differs_from_original():
    mutants = generate_mutants(PRICING.source)
    assert len(mutants) > 10
    for m in mutants:
        compile(m.mutant_source, "pricing.py", "exec")
        assert m.diff, f"{m.id} has no visible change"


def test_no_duplicate_mutants():
    sources = [m.mutant_source for m in generate_mutants(PRICING.source)]
    assert len(sources) == len(set(sources))


def test_boundary_operator_is_generated():
    descriptions = [m.description for m in generate_mutants("def f(x):\n    return x >= 10\n")]
    assert any("`>=` became `>`" in d for d in descriptions)


def test_code_outside_functions_is_not_mutated():
    assert generate_mutants("LIMIT = 10 + 5\n") == []


# ---------- runner and classification ----------

def test_original_code_passes_its_own_tests():
    assert run_tests(PRICING).passed


def _mutant(source):
    return Mutant("t1", "ROR", "test", "f", 1, source, "-a\n+b")


def test_survived_vs_killed():
    p = Project("t", "mod", "def f(x):\n    return x >= 10\n",
                "from mod import f\n\ndef test_f():\n    assert f(20)\n")
    weak = _mutant("def f(x):\n    return x > 10\n")       # f(20) still True
    caught = _mutant("def f(x):\n    return x < 10\n")     # f(20) becomes False
    evaluate(p, [weak, caught])
    assert weak.status == "survived"
    assert caught.status == "killed"


def test_infinite_loop_mutant_is_reported_as_timeout(monkeypatch):
    monkeypatch.setattr(runner, "TIMEOUT_S", 3)
    p = Project("t", "mod", "def f(n):\n    while n > 0:\n        n -= 1\n    return n\n",
                "from mod import f\n\ndef test_f():\n    assert f(3) == 0\n")
    looping = _mutant("def f(n):\n    while n > 0:\n        n += 1\n    return n\n")
    evaluate(p, [looping])
    assert looping.status == "timeout"


def test_syntax_error_mutant_is_invalid_and_excluded_from_score():
    p = Project("t", "mod", "def f(x):\n    return x\n",
                "from mod import f\n\ndef test_f():\n    assert f(1) == 1\n")
    broken = _mutant("def f(x)\n    return x\n")
    evaluate(p, [broken])
    assert broken.status == "invalid"
    assert mutation_score([broken]) is None


def test_mutation_score_formula():
    ms = [_mutant("") for _ in range(4)]
    for m, s in zip(ms, ["killed", "timeout", "survived", "invalid"]):
        m.status = s
    assert mutation_score(ms) == round(100 * 2 / 3, 1)   # invalid excluded


# ---------- test generation ----------

def test_generated_test_is_verified_against_original_and_mutant():
    p = Project("t", "mod", "def f(x: int) -> bool:\n    return x >= 10\n",
                "from mod import f\n\ndef test_f():\n    assert f(20)\n")
    weak = _mutant("def f(x: int) -> bool:\n    return x > 10\n")
    result = synthesize(p, weak)
    assert result.status == "verified"
    assert "10" in result.input      # it found the boundary


def test_equivalent_mutant_is_not_given_a_fake_test():
    p = Project("t", "mod", "def f(x: int) -> int:\n    return x * 1\n",
                "from mod import f\n\ndef test_f():\n    assert f(2) == 2\n")
    same = _mutant("def f(x: int) -> int:\n    return x // 1\n")   # identical for ints
    assert synthesize(p, same).status == "equivalent"
