import streamlit as st
import fieldflow_core as core

core.render_sidebar("Home")

# Basic lightweight "marketing-style" layout (no external assets needed)
st.markdown(
    '''
<style>
/* Card styling */
.ff-card {
  border: 1px solid rgba(49, 51, 63, 0.15);
  border-radius: 16px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
}
.ff-hero {
  border: 1px solid rgba(49, 51, 63, 0.15);
  border-radius: 20px;
  padding: 22px;
  background: linear-gradient(135deg, rgba(255,255,255,0.03), rgba(255,255,255,0.0));
}
.ff-muted { color: rgba(49, 51, 63, 0.65); }
</style>
''',
    unsafe_allow_html=True,
)

st.markdown('<div class="ff-hero">', unsafe_allow_html=True)

c1, c2 = st.columns([2, 1])
with c1:
    st.title("FieldFlow")
    st.markdown("### CPM-grade schedule math + RFIs + submittals + cost — **local-first**.")
    st.markdown(
        '<div class="ff-muted">Built for project controls workflows that need clarity and speed — without cloud storage, logins, or heavy infrastructure.</div>',
        unsafe_allow_html=True,
    )

    b1, b2, b3 = st.columns(3)
    with b1:
        st.page_link("pages/00_Workspace.py", label="Open Workspace", width="stretch")
    with b2:
        st.page_link("pages/06_Saved_Results.py", label="Saved Results", width="stretch")
    with b3:
        st.page_link("pages/12_Feedback.py", label="Leave Feedback", width="stretch")

with c2:
    # Keep a clean brand mark in-page even if top-left logo isn't visible in some Streamlit versions
    try:
        st.image("assets/FieldFlow_logo.png", width=220)
    except Exception:
        pass

st.markdown("</div>", unsafe_allow_html=True)

# Hero banner (lightweight graphic)
try:
    st.image("assets/hero_banner.png", use_container_width=True)
except Exception:
    pass


st.markdown("---")

st.subheader("What you can do here")

f1, f2, f3, f4 = st.columns(4)
with f1:
    st.markdown('<div class="ff-card"><b>Scheduling</b><br/><span class="ff-muted">Compute CPM, see critical path/float, crash to a target date, and compare scenarios.</span></div>', unsafe_allow_html=True)
    if st.button("Schedule What-Ifs", width="stretch", key="home_sched"):
        st.session_state["__ff_workspace_tab__"] = "Schedule"
        st.switch_page("pages/00_Workspace.py")
with f2:
    st.markdown('<div class="ff-card"><b>RFIs</b><br/><span class="ff-muted">Track RFIs, run aging, bind to activities, and simulate schedule risk.</span></div>', unsafe_allow_html=True)
    if st.button("RFI Manager", width="stretch", key="home_rfi"):
        st.session_state["__ff_workspace_tab__"] = "RFIs"
        st.switch_page("pages/00_Workspace.py")
with f3:
    st.markdown('<div class="ff-card"><b>Submittals</b><br/><span class="ff-muted">Compare spec vs submittal text, detect gaps, generate a register.</span></div>', unsafe_allow_html=True)
    if st.button("Submittal Checker", width="stretch", key="home_sub"):
        st.session_state["__ff_workspace_tab__"] = "Schedule"
        st.switch_page("pages/00_Workspace.py")
with f4:
    st.markdown('<div class="ff-card"><b>Cost</b><br/><span class="ff-muted">Loaded cost curves, unit-cost estimating, production-rate labor+equipment.</span></div>', unsafe_allow_html=True)
    if st.button("Cost Estimator", width="stretch", key="home_cost"):
        st.session_state["__ff_workspace_tab__"] = "Cost"
        st.switch_page("pages/00_Workspace.py")
st.markdown("---")

st.subheader("A simple workflow")

w1, w2, w3 = st.columns(3)
with w1:
    st.markdown('<div class="ff-card"><b>1) Load data</b><br/><span class="ff-muted">Upload a schedule CSV, enter RFIs, paste spec/submittal text.</span></div>', unsafe_allow_html=True)
with w2:
    st.markdown('<div class="ff-card"><b>2) Compute</b><br/><span class="ff-muted">CPM + crash, RFI impacts, cost layers, and comparisons.</span></div>', unsafe_allow_html=True)
with w3:
    st.markdown('<div class="ff-card"><b>3) Save & export</b><br/><span class="ff-muted">Save runs locally to SQLite and export CSV/JSON/ZIP.</span></div>', unsafe_allow_html=True)

st.markdown("---")

st.subheader("Learn & help")
l1, l2, l3 = st.columns(3)
with l1:
    st.page_link("pages/11_About.py", label="About FieldFlow", width="stretch")
with l2:
    st.page_link("pages/05_Settings_and_Examples.py", label="Settings & Examples", width="stretch")
with l3:
    st.page_link("pages/12_Feedback.py", label="Leave Feedback", width="stretch")

st.caption("FieldFlow runs local-only on this app instance (SQLite). No external logins or file sync.")
