"""Your Tests Are Lying: mutation testing with automatic, verified test generation."""
import os
import re
from pathlib import Path

import streamlit as st

from mutators import OPERATOR_NAMES, generate_mutants
from runner import Project, evaluate, line_coverage, load_sample, mutation_score, run_tests
from synth import Synthesis, synthesize, test_file, verify

SAMPLES = Path(__file__).parent / "samples"
ICON = {"killed": "🟢", "timeout": "🟡", "survived": "🔴", "invalid": "⚪", "pending": "⏳"}

st.set_page_config(page_title="Your Tests Are Lying", page_icon="🧪", layout="wide")


# ---------- sidebar: project + optional AI ----------
with st.sidebar:
    st.header("Project")
    choices = sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())
    pick = st.radio("Code to test", choices + ["Upload my own"], format_func=str.title)

    project = None
    if pick == "Upload my own":
        mod = st.file_uploader("Module (.py)", type="py")
        tst = st.file_uploader("Its pytest file (.py)", type="py")
        if mod and tst:
            name = Path(mod.name).stem
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                project = Project(name, name, mod.getvalue().decode("utf-8"),
                                  tst.getvalue().decode("utf-8"))
            else:
                st.error("Module file name must be a valid Python name, like my_module.py")
        st.caption("Your code runs on this computer in a temporary folder. "
                   "Only upload code you trust.")
    else:
        project = load_sample(SAMPLES / pick)

    st.divider()
    with st.expander("Optional: AI mode"):
        st.caption("Not required. Uses any OpenAI-compatible API, e.g. Ollama running "
                   "locally at http://localhost:11434/v1")
        ai_on = st.toggle("Enable AI")
        base_url = st.text_input("Base URL", os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1"))
        model = st.text_input("Model", os.environ.get("LLM_MODEL", ""))
        api_key = st.text_input("API key (blank for Ollama)", os.environ.get("LLM_API_KEY", ""),
                                type="password")

llm = None
if ai_on and model:
    try:
        from llm import LLM
        llm = LLM(base_url, api_key, model)
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"AI unavailable: {e}")

# reset results when the project changes
key = (project.module_name, hash(project.source), hash(project.tests)) if project else None
if st.session_state.get("project_key") != key:
    for k in ("mutants", "synth", "after"):
        st.session_state.pop(k, None)
    st.session_state["project_key"] = key


# ---------- header ----------
st.title("🧪 Your Tests Are Lying")
st.caption("Green tests and high coverage don't prove your tests can catch bugs. "
           "This tool plants bugs on purpose, checks whether your tests notice, "
           "then writes the missing tests and proves each one works.")

if project is None:
    st.info("Upload a module and its test file in the sidebar to begin.")
    st.stop()

with st.expander(f"Code under test: {project.module_name}.py and {project.test_filename}"):
    c1, c2 = st.columns(2)
    c1.code(project.source, language="python")
    c2.code(project.tests, language="python")


@st.cache_data(show_spinner=False)
def baseline(module_name, source, tests):
    p = Project(module_name, module_name, source, tests)
    return run_tests(p).passed, line_coverage(p)


passed, cov = baseline(project.module_name, project.source, project.tests)
if not passed:
    st.error("The existing tests already fail on the original code. Fix them first; "
             "mutation testing needs a green suite to start from.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Existing tests", "All pass ✅")
m2.metric("Line coverage", f"{cov}%" if cov is not None else "n/a")
score_box = m3.empty()
score_box.metric("Mutation score", "?")


# ---------- step 1: mutation analysis ----------
st.subheader("Step 1 · Plant bugs and run your tests")
st.caption("Operators: " + ", ".join(f"{k} = {v}" for k, v in OPERATOR_NAMES.items() if k != "AI"))

if st.button("Run mutation analysis", type="primary"):
    mutants = generate_mutants(project.source)
    if llm:
        try:
            from llm import ai_mutants
            with st.spinner("AI is planting extra realistic bugs..."):
                mutants += ai_mutants(llm, project.source, project.tests)
        except Exception as e:  # noqa: BLE001
            st.warning(f"AI mutants skipped: {e}")
    bar = st.progress(0.0, text="Running your tests against each mutant...")
    todo = [m for m in mutants if m.status == "pending"]
    evaluate(project, todo, on_progress=lambda d, t: bar.progress(d / t))
    bar.empty()
    st.session_state["mutants"] = mutants
    st.session_state.pop("synth", None)
    st.session_state.pop("after", None)

mutants = st.session_state.get("mutants")
if mutants:
    score = mutation_score(mutants)
    score_box.metric("Mutation score", f"{score}%")
    killed = [m for m in mutants if m.status == "killed"]
    timeouts = [m for m in mutants if m.status == "timeout"]
    invalid = [m for m in mutants if m.status == "invalid"]
    survived = [m for m in mutants if m.status == "survived"]

    if cov is not None and score is not None and score < cov:
        st.error(f"Your tests run **{cov}%** of the lines but catch only **{score}%** of the bugs. "
                 f"{len(survived)} buggy versions of your code passed every test.")

    st.write(f"**{len(killed)}** killed · **{len(timeouts)}** timed out · "
             f"**{len(survived)}** survived · {len(invalid)} invalid · {len(mutants)} total")
    st.caption("Mutation score = (killed + timed out) / (killed + timed out + survived). "
               "Invalid mutants are excluded.")
    show = st.radio("Show", ["Survived", "Caught", "All"], horizontal=True)
    for m in mutants:
        if (show == "Survived" and m.status != "survived") or \
           (show == "Caught" and m.status not in ("killed", "timeout")):
            continue
        with st.expander(f"{ICON[m.status]} {m.id} · {OPERATOR_NAMES[m.operator]} · {m.description}"):
            if m.diff:
                st.code(m.diff, language="diff")
            st.caption(m.detail)


# ---------- step 2: write and verify missing tests ----------
if mutants and any(m.status == "survived" for m in mutants):
    st.subheader("Step 2 · Write the missing tests")
    st.caption("For each surviving bug, search for an input where the correct code and the "
               "buggy code disagree, and turn it into a test. A test is kept only if it "
               "passes on the correct code AND fails on the bug.")
    if st.button("Generate and verify tests", type="primary"):
        results = []
        survivors = [m for m in mutants if m.status == "survived"]
        bar = st.progress(0.0)
        for i, m in enumerate(survivors, 1):
            r = synthesize(project, m)
            if r.status != "verified" and llm and m.diff:
                try:
                    from llm import ai_test
                    code = ai_test(llm, project.source, m)
                    ok, note = verify(project, m, code)
                    if ok:
                        r = Synthesis(m.id, "verified", test_code=code, note=note, source="ai")
                except Exception:  # noqa: BLE001
                    pass
            results.append(r)
            bar.progress(i / len(survivors), text=f"{m.id}: {r.status}")
        bar.empty()
        st.session_state["synth"] = results
        good = [r.test_code for r in results if r.status == "verified"]
        if good:
            fresh = generate_mutants(project.source)
            evaluate(project, fresh, extra_tests={"test_generated.py": test_file(project, good)})
            st.session_state["after"] = mutation_score(fresh)

results = st.session_state.get("synth")
if results:
    verified = [r for r in results if r.status == "verified"]
    equivalent = [r for r in results if r.status == "equivalent"]
    other = [r for r in results if r.status in ("rejected", "failed")]

    before = mutation_score(mutants)
    after = st.session_state.get("after")
    a, b, c = st.columns(3)
    a.metric("Verified new tests", len(verified))
    b.metric("Likely equivalent mutants", len(equivalent))
    if after is not None:
        c.metric("Mutation score after", f"{after}%", delta=f"{round(after - before, 1)} pts")

    for r in results:
        m = next(x for x in mutants if x.id == r.mutant_id)
        icon = {"verified": "✅", "equivalent": "🟰", "rejected": "❌", "failed": "⚠️"}[r.status]
        with st.expander(f"{icon} {m.id} · {m.description}"):
            st.code(m.diff, language="diff")
            if r.input:
                st.write(f"With input `{r.function}({r.input})` the correct code gives "
                         f"`{r.expected}` but the bug gives `{r.mutant_gives}`.")
            if r.test_code:
                st.code(r.test_code, language="python")
            st.caption(r.note + ("  (written by AI)" if r.source == "ai" else ""))

    if verified:
        full = test_file(project, [r.test_code for r in verified])
        st.download_button("⬇️ Download test_generated.py", full, file_name="test_generated.py")
    if equivalent:
        st.info("An equivalent mutant changes the code without changing what it does, so no "
                "test can ever catch it. Deciding this exactly is impossible in general; "
                "this tool flags it when a broad input search finds no difference, "
                "so treat it as likely, not proven.")
    if other:
        st.warning(f"{len(other)} survivors could not get a verified test automatically.")
