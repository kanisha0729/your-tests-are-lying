"""Rule-based mutation engine (no AI, no internet).

Walks the module's syntax tree and creates one mutant per change, using
classic mutation operators from software-testing research:

  ROR  relational operator replacement   >=  ->  >
  LCR  logical connector replacement     and ->  or
  AOR  arithmetic operator replacement   +   ->  -
  CRP  constant replacement              10  ->  11   (inside comparisons)
  RVR  return value replacement          return 0.2 -> return 0
  NEG  condition negation                if x: -> if not x:
"""
from __future__ import annotations

import ast
import copy
import difflib
from dataclasses import dataclass


@dataclass
class Mutant:
    id: str
    operator: str          # ROR, LCR, ... or AI
    description: str
    function: str | None
    line: int
    mutant_source: str
    diff: str
    origin: str = "rule"   # rule | ai
    status: str = "pending"  # pending | killed | timeout | survived | invalid
    detail: str = ""


OPERATOR_NAMES = {
    "ROR": "Relational operator replaced",
    "LCR": "Logical connector replaced",
    "AOR": "Arithmetic operator replaced",
    "CRP": "Constant in comparison changed",
    "RVR": "Return value replaced",
    "NEG": "Condition negated",
    "AI": "AI-planted realistic bug",
}

_SYM = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.Eq: "==",
        ast.NotEq: "!=", ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
        ast.And: "and", ast.Or: "or"}

_COMPARE_SWAPS = {ast.Gt: [ast.GtE], ast.GtE: [ast.Gt], ast.Lt: [ast.LtE],
                  ast.LtE: [ast.Lt], ast.Eq: [ast.NotEq], ast.NotEq: [ast.Eq]}
_BINOP_SWAPS = {ast.Add: [ast.Sub], ast.Sub: [ast.Add],
                ast.Mult: [ast.Div], ast.Div: [ast.Mult]}


def _is_number(node) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def _function_map(tree) -> dict[int, str]:
    """Map node id -> name of the function that contains it."""
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                owner[id(n)] = fn.name
    return owner


def _sites(tree):
    """Yield (walk_index, operator_code, payload, description) for every mutation site."""
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[id(c)] = p

    for i, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            for alt in _COMPARE_SWAPS.get(type(node.ops[0]), []):
                yield i, "ROR", alt, f"`{_SYM[type(node.ops[0])]}` became `{_SYM[alt]}`"
        elif isinstance(node, ast.BoolOp):
            alt = ast.Or if isinstance(node.op, ast.And) else ast.And
            yield i, "LCR", alt, f"`{_SYM[type(node.op)]}` became `{_SYM[alt]}`"
        elif isinstance(node, ast.BinOp):
            for alt in _BINOP_SWAPS.get(type(node.op), []):
                yield i, "AOR", alt, f"`{_SYM[type(node.op)]}` became `{_SYM[alt]}`"
        elif _is_number(node):
            parent = parents.get(id(node))
            if isinstance(parent, ast.Compare) and isinstance(node.value, int):
                yield i, "CRP", node.value + 1, f"`{node.value}` became `{node.value + 1}`"
            elif isinstance(parent, ast.Return):
                new = 1 if node.value == 0 else 0
                yield i, "RVR", new, f"returns `{new}` instead of `{node.value}`"
        elif isinstance(node, ast.If):
            yield i, "NEG", None, "`if` condition negated"


def _apply(tree, index, code, payload):
    mutated = copy.deepcopy(tree)
    node = list(ast.walk(mutated))[index]
    if code == "ROR":
        node.ops = [payload()]
    elif code in ("LCR", "AOR"):
        node.op = payload()
    elif code in ("CRP", "RVR"):
        node.value = payload
    elif code == "NEG":
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    return ast.fix_missing_locations(mutated)


def short_diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
    return "\n".join(l for l in lines
                     if (l.startswith("-") or l.startswith("+"))
                     and not l.startswith(("---", "+++")))


def generate_mutants(source: str, limit: int = 60) -> list[Mutant]:
    tree = ast.parse(source)
    baseline = ast.unparse(tree)
    owners = _function_map(tree)
    nodes = list(ast.walk(tree))
    seen, out = set(), []
    for index, code, payload, desc in _sites(tree):
        node = nodes[index]
        func = owners.get(id(node))
        if func is None:          # only mutate code inside functions
            continue
        mutated_src = ast.unparse(_apply(tree, index, code, payload))
        if mutated_src == baseline or mutated_src in seen:
            continue
        seen.add(mutated_src)
        out.append(Mutant(
            id=f"m{len(out) + 1:02d}", operator=code,
            description=f"Line {node.lineno} in {func}(): {desc}",
            function=func, line=node.lineno, mutant_source=mutated_src,
            diff=short_diff(baseline, mutated_src)))
        if len(out) >= limit:
            break
    return out
