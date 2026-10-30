"""Differential test search. Runs in its own process (killed on timeout).

Given the original module and one mutant, find an input where they behave
differently, and turn it into a pytest test that expects the ORIGINAL's
behaviour. Input: path to a JSON job file. Output: one JSON line on stdout.
"""
import ast
import builtins
import inspect
import itertools
import json
import math
import random
import sys
import types

MAX_COMBOS = 4000


def load(src, name):
    mod = types.ModuleType(name)
    exec(compile(src, f"{name}.py", "exec"), mod.__dict__)
    return mod


def constants(src):
    nums, strs = set(), set()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docstrings:
            v = node.value
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                nums.add(v)
            elif isinstance(v, str) and len(v) <= 30:
                strs.add(v)
    return nums, strs


def _ordered(*groups, cap=60):
    """Merge groups in priority order (boundaries first), dropping duplicates."""
    out = []
    for group in groups:
        for v in group:
            if v not in out:
                out.append(v)
    return out[:cap]


def candidates(annotation, nums, strs):
    if annotation is bool:
        return [True, False]
    if annotation is str:
        return _ordered(sorted(strs), ["", "x"])
    if annotation is float:
        base = sorted({float(n) for n in nums})
        return _ordered(base,
                        [b + d for b in base for d in (-0.01, 0.01)],
                        [b + d for b in base for d in (-1, 1)],
                        [0.0, 1.0, 100.0])
    # int, or no annotation: treat as int
    ints = sorted({int(n) for n in nums})
    return _ordered(ints, [i + d for i in ints for d in (-1, 1)], [0, 1, -1, 2])


def outcome(fn, args):
    try:
        return ("ok", fn(*args))
    except Exception as e:  # noqa: BLE001
        return ("raise", type(e).__name__)


def same(a, b):
    if a[0] != b[0]:
        return False
    if a[0] == "raise":
        return a[1] == b[1]
    x, y = a[1], b[1]
    if isinstance(x, float) or isinstance(y, float):
        try:
            return math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-12)
        except TypeError:
            return x == y
    return x == y


def literal(value):
    """repr that evaluates back to the same value, or None."""
    try:
        text = repr(value)
        return text if ast.literal_eval(text) == value else None
    except Exception:  # noqa: BLE001
        return None


def changed_functions(orig_src, mut_src):
    def defs(src):
        return {n.name: ast.dump(n) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    a, b = defs(orig_src), defs(mut_src)
    return [name for name in a if a.get(name) != b.get(name)]


def make_test(test_name, func, args, expected):
    call = f"{func}({', '.join(repr(a) for a in args)})"
    if expected[0] == "raise":
        exc = expected[1] if hasattr(builtins, expected[1]) else "Exception"
        return f"def {test_name}():\n    with pytest.raises({exc}):\n        {call}\n"
    value = expected[1]
    if isinstance(value, float):
        return f"def {test_name}():\n    assert {call} == pytest.approx({value!r})\n"
    text = literal(value)
    if text is None:
        return None
    if isinstance(value, bool):
        return f"def {test_name}():\n    assert {call} is {text}\n"
    return f"def {test_name}():\n    assert {call} == {text}\n"


def main():
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    name = job["module_name"]
    orig = load(job["original_source"], name)
    mut = load(job["mutant_source"], name)
    nums, strs = constants(job["original_source"])

    targets = changed_functions(job["original_source"], job["mutant_source"])
    others = [n for n, f in vars(orig).items()
              if inspect.isfunction(f) and f.__module__ == name and not n.startswith("_")
              and n not in targets]
    tried = 0
    rng = random.Random(0)
    for func in targets + others:
        f_orig, f_mut = getattr(orig, func, None), getattr(mut, func, None)
        if not (callable(f_orig) and callable(f_mut)):
            continue
        params = list(inspect.signature(f_orig).parameters.values())
        if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
            continue
        pools = [candidates(p.annotation, nums, strs) for p in params]
        combos = list(itertools.islice(itertools.product(*pools), 200_000))
        if len(combos) > MAX_COMBOS:
            combos = combos[:500] + rng.sample(combos[500:], MAX_COMBOS - 500)
        for args in combos:
            tried += 1
            a, b = outcome(f_orig, args), outcome(f_mut, args)
            if same(a, b):
                continue
            test = make_test(f"test_catches_{job['mutant_id']}_{func}", func, args, a)
            if test:
                print(json.dumps({"found": True, "function": func, "test": test,
                                  "input": ", ".join(repr(x) for x in args),
                                  "expected": a[1] if a[0] == "raise" else repr(a[1]),
                                  "mutant_gives": b[1] if b[0] == "raise" else repr(b[1]),
                                  "tried": tried}))
                return
    print(json.dumps({"found": False, "tried": tried}))


if __name__ == "__main__":
    main()
