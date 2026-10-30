# Your Tests Are Lying

A green test suite is not proof your code is correct. This agent plants realistic,
human-style bugs in your code using **NVIDIA Nemotron on Nebius Token Factory**, runs
your existing tests against each one, and shows you which bugs slip through. Then it
writes the missing tests and **proves** each one: a generated test is accepted only if it
passes on the correct code and fails on the buggy version.

Track: Coding and Agentic Engineering

## How it works

1. **Bug planter (Nemotron 3 Super).** Reads the module and its tests, then proposes
   bugs a real developer would make (off-by-one, flipped boolean, wrong boundary),
   aimed at behaviour the tests don't pin down. Each bug is an exact text patch.
2. **Execution engine (`engine.py`).** Validates every patch (must match the source
   exactly once, must compile, no imports/IO/eval), applies it in an isolated temp copy,
   and runs pytest with a timeout. Suite fails = bug caught. Suite passes = your tests lied.
3. **Test writer (Nemotron 3 Super).** For each surviving bug, writes one targeted test.
4. **Verifier.** Runs the new test on the original code (must pass) and on the mutant
   (must fail). Rejected tests are sent back to the model with the reason, up to 2 retries.

The model never executes commands. It can only propose patches and test files, and
both are validated and sandboxed in a throwaway copy before they run.

## Run it

```bash
pip install -r requirements.txt
export NEBIUS_API_KEY=your_key        # Windows PowerShell: $env:NEBIUS_API_KEY="your_key"
streamlit run app.py
```

Optional overrides: `MUTATOR_MODEL`, `TESTWRITER_MODEL`, `NEBIUS_BASE_URL`.

## NVIDIA and Nebius usage

- Models: `nvidia/nemotron-3-super-120b-a12b` for bug planting and test writing,
  served through Nebius Token Factory's OpenAI-compatible API.
- TODO before submitting: list any other Nebius services used.

## Limitations (honest)

- Works on the built-in sample project only. Arbitrary repo upload needs real
  sandboxing (containers, no network) and is future work.
- Bugs are single-snippet patches; multi-line and cross-file bugs are future work.

## License

MIT
