"""Your Tests Are Lying: Streamlit front end.

Flow: Nemotron plants realistic bugs -> engine runs the existing tests on each
mutant -> for every bug that slipped through, Nemotron writes a test -> engine
accepts the test only if it PASSES on the correct code and FAILS on the bug.

engine.py (execution + verification) and agent.py (Nemotron calls) are unchanged.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import streamlit as st

import agent
from engine import (Mutation, Verification, evaluate_mutation, read_source,
                    read_tests, run_suite, verify_generated_test)

MAX_RETRIES = 2                 # extra attempts per surviving bug
MAX_LIVE_RUNS_PER_SESSION = 3   # protects API credits while judges test
MAX_LIVE_RUNS_PER_HOUR = 30     # across all visitors
EXAMPLE_FILE = Path(__file__).parent / "example_run.json"
BANNED_IN_TESTS = ("import os", "from os", "import subprocess", "import socket",
                   "import shutil", "import requests", "urllib", "open(",
                   "__import__", "eval(", "exec(")

st.set_page_config(page_title="Your Tests Are Lying", page_icon="🧪", layout="wide")

# ---------------------------------------------------------------- styling
st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1180px;}
.hero h1 {font-size: 2.6rem; margin-bottom: .2rem;}
.hero p {font-size: 1.08rem; opacity: .85; max-width: 820px;}
.pill {display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem;
       font-weight: 600; margin-right: 6px; border: 1px solid rgba(128,128,128,.35);}
.pill-red {background: rgba(239,68,68,.15); color:#f87171; border-color: rgba(239,68,68,.4);}
.pill-green {background: rgba(34,197,94,.15); color:#4ade80; border-color: rgba(34,197,94,.4);}
.pill-grey {background: rgba(148,163,184,.15); color:#cbd5e1;}
.pill-blue {background: rgba(59,130,246,.15); color:#93c5fd; border-color: rgba(59,130,246,.4);}
.step {border:1px solid rgba(128,128,128,.25); border-radius: 12px; padding: 14px 16px; height: 100%;}
.step b {font-size: 1.02rem;} .step span {opacity:.8; font-size:.92rem;}
.score {border-radius: 14px; padding: 18px 20px; border: 1px solid rgba(128,128,128,.25);}
.score .big {font-size: 2.6rem; font-weight: 800; line-height: 1.1;}
.score .lbl {opacity: .8; font-size: .92rem;}
.score-bad {background: rgba(239,68,68,.08); border-color: rgba(239,68,68,.35);}
.score-good {background: rgba(34,197,94,.08); border-color: rgba(34,197,94,.35);}
.arrow {font-size: 2.2rem; text-align:center; padding-top: 30px; opacity:.6;}
.small {opacity:.75; font-size:.86rem;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
@st.cache_data(show_spinner=False)
def baseline() -> tuple[bool, int]:
    r = run_suite()
    m = re.search(r"(\d+) passed", r.output)
    return r.passed, int(m.group(1)) if m else 0


@st.cache_resource
def _global_budget() -> dict:
    return {"window_start": time.time(), "count": 0}


def live_budget_left() -> tuple[bool, str]:
    used = st.session_state.get("live_runs", 0)
    if used >= MAX_LIVE_RUNS_PER_SESSION:
        return False, f"Live run limit reached for this session ({MAX_LIVE_RUNS_PER_SESSION}). The example run is still available."
    g = _global_budget()
    if time.time() - g["window_start"] > 3600:
        g["window_start"], g["count"] = time.time(), 0
    if g["count"] >= MAX_LIVE_RUNS_PER_HOUR:
        return False, "The demo is busy right now (hourly limit reached). Try the example run or come back later."
    return True, ""


def has_api_key() -> bool:
    return bool(os.environ.get("NEBIUS_API_KEY"))


def safety_problem(test_code: str) -> str | None:
    for b in BANNED_IN_TESTS:
        if b in test_code:
            return f"rejected before running: test contains disallowed code ({b.strip()})"
    return None


def load_example() -> dict | None:
    if not EXAMPLE_FILE.exists():
        return None
    data = json.loads(EXAMPLE_FILE.read_text(encoding="utf-8"))
    muts = [Mutation(**m) for m in data["mutations"]]
    by_id = {m.id: m for m in muts}
    verifs = []
    for v in data["verifications"]:
        ver = Verification(mutation_id=v["mutation_id"], test_code=v["test_code"],
                           passes_on_original=v["passes_on_original"],
                           fails_on_mutant=v["fails_on_mutant"], note=v["note"])
        verifs.append({"mutation": by_id[v["mutation_id"]], "v": ver,
                       "reasoning": v.get("reasoning", ""), "attempts": v.get("attempts", 1)})
    return {"mode": "example", "label": data.get("label", "Example run"),
            "muts": muts, "verifs": verifs, "timings": {}}


def run_to_json(run: dict) -> str:
    return json.dumps({
        "label": "Saved run",
        "mutations": [m.__dict__ for m in run["muts"]],
        "verifications": [{"mutation_id": x["mutation"].id, "test_code": x["v"].test_code if x["v"] else "",
                           "passes_on_original": bool(x["v"] and x["v"].passes_on_original),
                           "fails_on_mutant": bool(x["v"] and x["v"].fails_on_mutant),
                           "note": x["v"].note if x["v"] else x.get("error", ""),
                           "reasoning": x["reasoning"], "attempts": x["attempts"]}
                          for x in run["verifs"]],
    }, indent=2)


def verified_tests_zip(run: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        n = 0
        for x in run["verifs"]:
            if x["v"] and x["v"].valid:
                n += 1
                header = f'"""Catches: {x["mutation"].title}\nVerified: passes on correct code, fails on the bug."""\n'
                z.writestr(f"tests/test_generated_{n}_{x['mutation'].id}.py", header + x["v"].test_code)
    return buf.getvalue()


def run_live(source: str, tests: str) -> dict:
    timings: dict[str, float] = {}
    status = st.status("Running the agent with NVIDIA Nemotron...", expanded=True)
    try:
        return _run_live_steps(source, tests, status, timings)
    except Exception:
        status.update(label="Live run stopped with an error", state="error", expanded=False)
        raise


def _run_live_steps(source: str, tests: str, status, timings: dict) -> dict:
    with status:
        t0 = time.time()
        st.write("🧠 **Planting realistic bugs** with Nemotron...")
        muts = agent.plant_bugs(source, tests)
        timings["plant"] = time.time() - t0
        st.write(f"Nemotron proposed **{len(muts)} bugs** in {timings['plant']:.1f}s.")

        t1 = time.time()
        st.write("🧪 **Running your existing tests** against every buggy version...")
        bar = st.progress(0.0)
        for i, m in enumerate(muts, 1):
            evaluate_mutation(m, source)
            bar.progress(i / max(len(muts), 1))
        timings["evaluate"] = time.time() - t1
        survived = [m for m in muts if m.status == "survived"]
        st.write(f"**{len(survived)}** bug(s) slipped past your tests.")

        verifs = []
        t2 = time.time()
        for m in survived:
            st.write(f"✍️ Writing and proving a test for: *{m.title}*")
            err, v, reason, attempts = "", None, "", 0
            for _ in range(MAX_RETRIES + 1):
                attempts += 1
                try:
                    code, reason = agent.write_test(source, tests, m, err)
                except Exception as e:  # bad JSON, API hiccup: retry with the reason
                    err = f"bad model output: {e}"
                    continue
                problem = safety_problem(code)
                if problem:
                    v = Verification(mutation_id=m.id, test_code=code, note=problem,
                                     extra={"blocked": True})
                    err = problem
                    continue
                v = verify_generated_test(m, code, source)
                if v.valid:
                    break
                err = v.note
            verifs.append({"mutation": m, "v": v, "reasoning": reason,
                           "attempts": attempts, "error": err if not v else ""})
        timings["write"] = time.time() - t2
        ok = sum(1 for x in verifs if x["v"] and x["v"].valid)
        status.update(label=f"Done: {ok}/{len(survived)} surviving bugs now caught by verified tests.",
                      state="complete", expanded=False)
    return {"mode": "live", "label": f"Live run with {agent.MUTATOR_MODEL}",
            "muts": muts, "verifs": verifs, "timings": timings}


# ---------------------------------------------------------------- header
source, tests = read_source(), read_tests()
base_ok, base_count = baseline()

st.markdown("""
<div class="hero">
  <h1>🧪 Your Tests Are Lying</h1>
  <p>A green test suite is not proof your code works. This agent uses <b>NVIDIA Nemotron on Nebius
  Token Factory</b> to plant realistic, human-style bugs, shows which ones your tests miss, then writes
  the missing tests and <b>proves</b> each one catches its bug.</p>
</div>
""", unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
c1.markdown('<div class="step"><b>1 · Plant</b><br><span>Nemotron reads the code and its tests, '
            'then plants bugs a tired developer would really make.</span></div>', unsafe_allow_html=True)
c2.markdown('<div class="step"><b>2 · Expose</b><br><span>The engine runs your existing tests on every '
            'buggy copy. If they still pass, your tests lied.</span></div>', unsafe_allow_html=True)
c3.markdown('<div class="step"><b>3 · Fix and prove</b><br><span>Nemotron writes a test per surviving bug. '
            'It is kept only if it passes on correct code and fails on the bug.</span></div>',
            unsafe_allow_html=True)

st.write("")
with st.sidebar:
    st.markdown("### About")
    st.write("Track: **Coding and Agentic Engineering**")
    st.markdown(f"<span class='pill pill-blue'>Model</span> `{agent.MUTATOR_MODEL}`", unsafe_allow_html=True)
    st.markdown("<span class='pill pill-blue'>Platform</span> Nebius Token Factory", unsafe_allow_html=True)
    st.markdown("### Safety")
    st.caption("The model never runs commands. Bugs are exact text patches validated by the engine. "
               "Generated tests are screened for disallowed code, then run in a throwaway copy "
               "with a timeout.")

# ---------------------------------------------------------------- code under test
st.subheader("The code under test")
st.markdown(
    f"<span class='pill {'pill-green' if base_ok else 'pill-red'}'>"
    f"{'✓' if base_ok else '✗'} {base_count} existing tests {'pass' if base_ok else 'fail'}</span>"
    "<span class='small'>A small online-store pricing module. Its tests are green. "
    "Looks healthy. Is it?</span>", unsafe_allow_html=True)
with st.expander("Show pricing.py and its existing tests"):
    t_code, t_tests = st.tabs(["pricing.py", "tests/test_pricing.py"])
    t_code.code(source, language="python")
    t_tests.code(tests, language="python")

# ---------------------------------------------------------------- controls
st.write("")
can_live, why_not = live_budget_left()
b1, b2, _ = st.columns([1.3, 1, 2])
live_clicked = b1.button("▶ Run live with Nemotron", type="primary", use_container_width=True,
                         disabled=not (has_api_key() and can_live))
example_clicked = b2.button("📼 Show example run", use_container_width=True)
if not has_api_key():
    st.caption("Live mode needs a Nebius Token Factory key (NEBIUS_API_KEY). The example run works without one.")
elif not can_live:
    st.caption(why_not)

if live_clicked:
    st.session_state["live_runs"] = st.session_state.get("live_runs", 0) + 1
    _global_budget()["count"] += 1
    try:
        st.session_state["run"] = run_live(source, tests)
    except Exception as e:
        st.session_state.pop("run", None)
        st.error(f"Live run failed: {e}")
        st.info("You can still see the full flow with **📼 Show example run**.")

if example_clicked:
    ex = load_example()
    if ex:
        st.session_state["run"] = ex
    else:
        st.error("example_run.json is missing.")

run = st.session_state.get("run")

# ---------------------------------------------------------------- results
if run:
    st.divider()
    if run["mode"] == "example":
        st.info(f"📼 {run['label']}")
    else:
        t = run.get("timings", {})
        total = sum(t.values())
        st.success(f"⚡ {run['label']} · finished in {total:.0f}s")

    muts = run["muts"]
    valid = [m for m in muts if m.status != "invalid"]
    survived = [m for m in valid if m.status == "survived"]
    killed = [m for m in valid if m.status == "killed"]
    invalid = [m for m in muts if m.status == "invalid"]
    verified = [x for x in run["verifs"] if x["v"] and x["v"].valid]
    caught_after = len(killed) + len(verified)
    n = max(len(valid), 1)

    st.subheader("Result")
    s1, s_arrow, s2 = st.columns([5, 1, 5])
    s1.markdown(f"""<div class="score score-bad"><div class="lbl">Bugs caught by your original tests</div>
        <div class="big">{len(killed)} / {len(valid)}</div>
        <div class="lbl">{len(survived)} realistic bug(s) slipped through · {round(100*len(killed)/n)}% mutation score</div></div>""",
                unsafe_allow_html=True)
    s_arrow.markdown('<div class="arrow">→</div>', unsafe_allow_html=True)
    s2.markdown(f"""<div class="score score-good"><div class="lbl">After the agent's verified tests</div>
        <div class="big">{caught_after} / {len(valid)}</div>
        <div class="lbl">{len(verified)} new test(s), each proven to catch its bug · {round(100*caught_after/n)}% mutation score</div></div>""",
                unsafe_allow_html=True)
    if survived:
        st.error(f"Your test suite passed on **{len(survived)} buggy version(s)** of this code. "
                 "Those tests were lying.")

    tab_bugs, tab_tests = st.tabs([f"🐞 Planted bugs ({len(muts)})",
                                   f"✅ Generated tests ({len(verified)}/{len(run['verifs'])} verified)"])

    with tab_bugs:
        st.caption("Each bug is a one-line patch. 🔴 slipped past your tests · 🟢 caught · ⚪ rejected by the engine (bad patch).")
        order = {"survived": 0, "killed": 1, "invalid": 2}
        for m in sorted(muts, key=lambda m: order.get(m.status, 3)):
            icon, pill, label = {"survived": ("🔴", "pill-red", "SLIPPED THROUGH"),
                                 "killed": ("🟢", "pill-green", "CAUGHT"),
                                 "invalid": ("⚪", "pill-grey", "REJECTED PATCH")}.get(
                m.status, ("⚪", "pill-grey", m.status.upper()))
            with st.container(border=True):
                st.markdown(f"{icon} **{m.title}** &nbsp; <span class='pill {pill}'>{label}</span>",
                            unsafe_allow_html=True)
                st.code(f"- {m.original}\n+ {m.mutated}", language="diff")
                if m.why_realistic:
                    st.markdown(f"<span class='small'>Why a developer would write this: {m.why_realistic}</span>",
                                unsafe_allow_html=True)
                st.caption(m.detail)

    with tab_tests:
        if not run["verifs"]:
            st.write("No bugs slipped through, so no new tests were needed. Your tests told the truth this time.")
        for x in run["verifs"]:
            m, v = x["mutation"], x["v"]
            good = bool(v and v.valid)
            with st.container(border=True):
                st.markdown(f"{'✅' if good else '❌'} **Catches: {m.title}**", unsafe_allow_html=True)
                if v:
                    attempt_pill = f"<span class='pill pill-grey'>attempt {x['attempts']} of {MAX_RETRIES + 1}</span>"
                    if v.extra.get("blocked"):
                        st.markdown("<span class='pill pill-red'>⛔ blocked by safety check, never run</span>"
                                    + attempt_pill, unsafe_allow_html=True)
                    else:
                        po = "pill-green" if v.passes_on_original else "pill-red"
                        fm = "pill-green" if v.fails_on_mutant else "pill-red"
                        fm_text = (("✓" if v.fails_on_mutant else "✗") + " fails on the bug"
                                   if v.passes_on_original else "· not run on the bug")
                        st.markdown(
                            f"<span class='pill {po}'>{'✓' if v.passes_on_original else '✗'} passes on correct code</span>"
                            f"<span class='pill {fm if v.passes_on_original else 'pill-grey'}'>{fm_text}</span>"
                            + attempt_pill, unsafe_allow_html=True)
                    if x["reasoning"]:
                        who = "Nemotron's reasoning" if run["mode"] == "live" else "Reasoning"
                        st.markdown(f"<span class='small'>{who}: {x['reasoning']}</span>",
                                    unsafe_allow_html=True)
                    st.code(v.test_code, language="python")
                    if not good:
                        hint = (" · If no test can tell the two versions apart, the 'bug' may not "
                                "change behaviour at all." if "does not detect" in v.note else "")
                        st.caption(v.note + hint)
                else:
                    st.caption(f"No usable test after {x['attempts']} attempts: {x.get('error', '')}")

        if verified:
            st.download_button("⬇ Download verified tests (.zip)", data=verified_tests_zip(run),
                               file_name="verified_tests.zip", mime="application/zip")

    if invalid:
        st.caption(f"{len(invalid)} proposed patch(es) were rejected by the engine before running "
                   "(snippet not found, does not compile, or disallowed code).")
    if run["mode"] == "live":
        st.download_button("Save this run as JSON", data=run_to_json(run),
                           file_name="example_run.json", mime="application/json")

st.divider()
st.caption("Built for the Nebius x NVIDIA Global AI Hackathon · Coding and Agentic Engineering track · "
           "MIT licensed")

with st.sidebar:  # rendered last so the count includes a run started on this click
    left = MAX_LIVE_RUNS_PER_SESSION - st.session_state.get("live_runs", 0)
    st.caption(f"Live runs left in this session: {max(left, 0)} / {MAX_LIVE_RUNS_PER_SESSION}")
