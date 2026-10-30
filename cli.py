"""Command-line version: python cli.py samples/pricing  [--write-tests]"""
import argparse
from pathlib import Path

from mutators import generate_mutants
from runner import evaluate, line_coverage, load_sample, mutation_score, run_tests
from synth import synthesize, test_file


def main():
    ap = argparse.ArgumentParser(description="Mutation testing with verified test generation")
    ap.add_argument("folder", help="folder with module.py and test_module.py")
    ap.add_argument("--write-tests", action="store_true",
                    help="generate tests for survivors and save test_generated.py")
    args = ap.parse_args()

    project = load_sample(Path(args.folder))
    if not run_tests(project).passed:
        raise SystemExit("Existing tests fail on the original code. Fix them first.")
    cov = line_coverage(project)
    mutants = evaluate(project, generate_mutants(project.source))
    survivors = [m for m in mutants if m.status == "survived"]

    print(f"\nModule: {project.module_name}.py")
    print(f"Line coverage:   {cov}%")
    print(f"Mutation score:  {mutation_score(mutants)}%  "
          f"({len(mutants) - len(survivors)} caught, {len(survivors)} survived)\n")
    for m in survivors:
        print(f"  SURVIVED {m.id}  {m.description}")

    if args.write_tests and survivors:
        results = [synthesize(project, m) for m in survivors]
        good = [r.test_code for r in results if r.status == "verified"]
        out = Path(args.folder) / "test_generated.py"
        out.write_text(test_file(project, good), encoding="utf-8")
        after = mutation_score(evaluate(project, generate_mutants(project.source),
                                        extra_tests={"test_generated.py": out.read_text()}))
        print(f"\n{len(good)} verified tests written to {out}")
        print(f"Mutation score after: {after}%")


if __name__ == "__main__":
    main()
