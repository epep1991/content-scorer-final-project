import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
from app.scorer import score_all, readiness_tier
from app.channels import CHANNELS


def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return ""

st.set_page_config(
    page_title="Content Modularization Readiness Scorer",
    page_icon="📋",
    layout="wide",
)

TIER_COLORS = {"PASS": "#22c55e", "PARTIAL": "#f59e0b", "FAIL": "#ef4444"}
STATUS_COLORS = {"pass": "#22c55e", "embedded": "#f59e0b", "dependent": "#f59e0b", "missing": "#ef4444"}
STATUS_EMOJI = {"pass": "✅", "embedded": "⚠️", "dependent": "⚠️", "missing": "❌"}
COMPONENTS = ["headline", "short_description", "feature_list", "audience_statement", "cta"]


def tier_badge(tier):
    color = TIER_COLORS.get(tier, "#6b7280")
    return f'<span style="background:{color};color:white;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600">{tier}</span>'


def status_badge(status):
    color = STATUS_COLORS.get(status, "#6b7280")
    emoji = STATUS_EMOJI.get(status, "")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:8px;font-size:12px">{emoji} {status.upper()}</span>'


# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ Settings")

    uploaded_file = st.file_uploader("Upload your CMS export", type="csv")

    channel_name = st.selectbox(
        "Select channel",
        options=list(CHANNELS.keys()),
        help="Character limits and scoring criteria are applied per channel.",
    )
    channel = CHANNELS[channel_name]

    limits = channel["char_limits"]
    with st.expander("Channel limits", expanded=False):
        for k, v in limits.items():
            st.markdown(f"**{v['field']}** (`{k}`): {v['limit']} chars  \n<small>{v['note']}</small>", unsafe_allow_html=True)

    run_baseline = st.checkbox(
        "Run baseline comparison",
        value=False,
        help="Also scores each page with a prompt-only approach for side-by-side comparison. Doubles API calls.",
    )

    run_btn = st.button("▶ Run Scorer", type="primary", disabled=not uploaded_file)

    st.markdown("---")
    st.caption("Evaluates 5 components per page: headline, short description, feature list, audience statement, CTA.")


# ── Main ───────────────────────────────────────────────────────────────────────

st.title("Content Modularization Readiness Scorer")
st.markdown(f"*Pre-flight check for AI-powered {channel['platform']} {channel['placement']} ad generation*")

if not uploaded_file:
    st.info("Upload a CSV export from your CMS in the sidebar to get started.")
    st.stop()

df = pd.read_csv(uploaded_file)
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

preview_cols = [c for c in ["product_name", "body_content"] if c in df.columns]
st.markdown(f"**{len(df)} products loaded.**")
st.dataframe(df[preview_cols].head(3), use_container_width=True)

if run_btn:
    rows = df.to_dict("records")
    progress_bar = st.progress(0)
    status_text = st.empty()

    def on_progress(done, total, name):
        progress_bar.progress(done / total)
        status_text.text(f"Scoring {done}/{total}: {name}")

    with st.spinner("Calling Anthropic API..."):
        results = score_all(
            rows, get_api_key(), channel,
            run_baseline=run_baseline,
            progress_callback=on_progress,
        )

    progress_bar.empty()
    status_text.empty()
    st.session_state["results"] = results
    st.session_state["channel_name"] = channel_name
    st.session_state["run_baseline"] = run_baseline

if "results" not in st.session_state:
    st.stop()

results = st.session_state["results"]
run_baseline = st.session_state.get("run_baseline", False)

# ── Summary metrics ────────────────────────────────────────────────────────────

st.markdown("---")
total = len(results)
ready = sum(1 for r in results if r["pipeline_ready"])
pass_count = sum(1 for r in results if readiness_tier(r["passing_count"]) == "PASS")
partial_count = sum(1 for r in results if readiness_tier(r["passing_count"]) == "PARTIAL")
fail_count = sum(1 for r in results if readiness_tier(r["passing_count"]) == "FAIL")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Pages", total)
c2.metric("Pipeline Ready", ready, help="All 5 components pass + metadata complete")
c3.metric("Needs Remediation", total - ready)
c4.metric("PASS / PARTIAL / FAIL", f"{pass_count} / {partial_count} / {fail_count}")

# ── Results table ──────────────────────────────────────────────────────────────

st.markdown("### Results")
st.caption("Sorted by readiness score. Expand a row to see component details.")

sorted_results = sorted(results, key=lambda r: r["passing_count"], reverse=True)

for r in sorted_results:
    tier = readiness_tier(r["passing_count"])
    meta_icon = "✅" if r["metadata_complete"] else "❌"
    expected = r.get("expected_result", "")
    match = "✅" if tier == expected else "❌" if expected else ""

    header = (
        f"{tier_badge(tier)}&nbsp;&nbsp;"
        f"**{r['product_name']}** &nbsp;—&nbsp; "
        f"{r['passing_count']}/5 components &nbsp;|&nbsp; "
        f"Metadata {meta_icon}"
        + (f" &nbsp;|&nbsp; Ground truth: `{expected}` {match}" if expected else "")
    )

    with st.expander(r["product_name"], expanded=False):
        st.markdown(header, unsafe_allow_html=True)
        st.markdown("#### Component Scores")

        cols = st.columns([2, 1, 4])
        cols[0].markdown("**Component**")
        cols[1].markdown("**Status**")
        cols[2].markdown("**Reason**")

        for comp in COMPONENTS:
            data = r["components"].get(comp, {"status": "missing", "reason": "Not returned by scorer"})
            status = data.get("status", "missing")
            reason = data.get("reason") or ""
            note = data.get("char_limit_note", "")
            cols = st.columns([2, 1, 4])
            cols[0].markdown(f"`{comp}`")
            cols[1].markdown(status_badge(status), unsafe_allow_html=True)
            cols[2].markdown(f"{reason}" + (f"<br><small>*{note}*</small>" if note else ""), unsafe_allow_html=True)

        st.markdown("#### Metadata")
        meta_cols = st.columns(len(r["metadata"]))
        for i, (field, present) in enumerate(r["metadata"].items()):
            meta_cols[i].markdown(f"{'✅' if present else '❌'} `{field}`")

        if run_baseline and r.get("baseline_feedback"):
            st.markdown("#### Baseline Comparison")
            col_rubric, col_baseline = st.columns(2)
            with col_rubric:
                st.markdown("**Rubric Scorer (this tool)**")
                for comp in COMPONENTS:
                    data = r["components"].get(comp, {})
                    status = data.get("status", "missing")
                    reason = data.get("reason") or "—"
                    st.markdown(f"- `{comp}`: **{status}** — {reason}")
            with col_baseline:
                st.markdown("**Prompt-Only Baseline**")
                st.markdown(r["baseline_feedback"])
