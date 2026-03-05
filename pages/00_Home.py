import pathlib
import urllib.parse

import streamlit as st

import fieldflow_core as core


def _svg_icon(svg_rel_path: str, width: int = 72):
    """Render an SVG icon (transparent background) from a local file."""
    svg_path = pathlib.Path(svg_rel_path)
    if not svg_path.exists():
        return
    svg_text = svg_path.read_text(encoding="utf-8")
    data = urllib.parse.quote(svg_text)
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;justify-content:center;margin:0.25rem 0 0.5rem 0;">
          <img src="data:image/svg+xml;utf8,{data}" width="{width}" height="{width}" />
        </div>
        """,
        unsafe_allow_html=True,
    )


core.set_page_config("Home")
core.render_sidebar(active="Home")
core.primary_action_bar(
    actions=[
        ("Open Workspace", "pages/10_Workspace.py"),
        ("Saved Results", "pages/11_Saved_Results.py"),
        ("Leave Feedback", "pages/98_Leave_Feedback.py"),
    ]
)

st.title("FieldFlow")
st.subheader("CPM-grade schedule math + RFIs + submittals + cost — local-first.")
st.caption(
    "Built for project controls workflows that need clarity and speed — without cloud storage, logins, or heavy infrastructure."
)

st.divider()

st.header("What you can do here")
cols = st.columns(4)

with cols[0]:
    _svg_icon("assets/icons/cpm_scheduling.svg")
    st.markdown(
        """**Scheduling**  
Compute CPM, see critical path/float, crash to target, and compare scenarios."""
    )
    if st.button("Open Scheduling", key="home_sched"):
        st.switch_page("pages/02_Schedule_What_Ifs.py")

with cols[1]:
    _svg_icon("assets/icons/rfi_tracking.svg")
    st.markdown(
        """**RFIs**  
Track RFIs, run aging, bind to activities, and simulate schedule risk."""
    )
    if st.button("Open RFIs", key="home_rfi"):
        st.switch_page("pages/03_RFI_Manager.py")

with cols[2]:
    _svg_icon("assets/icons/rfi_tracking.svg", width=70)
    st.markdown(
        """**Submittals**  
Compare spec vs submittal text, detect gaps, generate a register."""
    )
    if st.button("Open Submittals", key="home_sub"):
        st.switch_page("pages/01_Submittal_Checker.py")

with cols[3]:
    _svg_icon("assets/icons/cost_analysis.svg")
    st.markdown(
        """**Cost**  
Loaded cost curves, unit-cost estimating, production-rate labor+equipment."""
    )
    if st.button("Open Cost", key="home_cost"):
        st.switch_page("pages/09_Cost_Estimator.py")

st.divider()

st.header("A simple workflow")
step1, step2, step3 = st.columns(3)
with step1:
    st.markdown("**1) Load data**")
    st.caption("Upload a schedule CSV, enter RFIs, paste spec/submittal text.")
with step2:
    st.markdown("**2) Compute**")
    st.caption("CPM + crash, RFI impacts, cost layers, and comparisons.")
with step3:
    st.markdown("**3) Save & export**")
    st.caption("Save runs locally to SQLite and export CSV/JSON/ZIP.")

st.divider()

st.header("Learn & help")
links = st.columns(3)
links[0].page_link("pages/97_About.py", label="About FieldFlow")
links[1].page_link("pages/99_Settings_and_Examples.py", label="Settings & Examples")
links[2].page_link("pages/98_Leave_Feedback.py", label="Leave Feedback")

st.caption(
    "FieldFlow runs local-only on this app instance (SQLite). No external logins or file sync."
)
