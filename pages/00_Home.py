import streamlit as st
import fieldflow_core as core

core.render_sidebar("Home")

st.title("FieldFlow")
st.subheader("CPM-grade schedule math + RFIs + submittals + cost — local-first.")
st.caption(
    "Built for project controls workflows that need clarity and speed — without cloud storage, logins, or heavy infrastructure."
)

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("Open Workspace", use_container_width=True):
        st.switch_page("pages/00_Workspace.py")
with c2:
    if st.button("Saved Results", use_container_width=True):
        st.switch_page("pages/06_Saved_Results.py")
with c3:
    if st.button("Leave Feedback", use_container_width=True):
        st.switch_page("pages/12_Feedback.py")

st.markdown("---")

st.header("What you can do here")
a, b, c, d = st.columns(4)
with a:
    st.markdown("""**Scheduling**  
Compute CPM, see critical path/float, crash to target, and compare scenarios.""")
    if st.button("Open Scheduling", key="home_sched"):
        st.switch_page("pages/02_Schedule_What_Ifs.py")
with b:
    st.markdown("""**RFIs**  
Track RFIs, run aging, bind to activities, and simulate schedule risk.""")
    if st.button("Open RFIs", key="home_rfi"):
        st.switch_page("pages/03_RFI_Manager.py")
with c:
    st.markdown("""**Submittals**  
Compare spec vs submittal text, detect gaps, generate a register.""")
    if st.button("Open Submittals", key="home_sub"):
        st.switch_page("pages/01_Submittal_Checker.py")
with d:
    st.markdown("""**Cost**  
Loaded cost curves, unit-cost estimating, production-rate labor+equipment.""")
    if st.button("Open Cost", key="home_cost"):
        st.session_state["__ff_workspace_tab__"] = "Cost"
        st.switch_page("pages/00_Workspace.py")

st.markdown("---")

st.header("A simple workflow")
w1, w2, w3 = st.columns(3)
with w1:
    st.markdown("""**1) Load data**  
Upload a schedule CSV, enter RFIs, paste spec/submittal text.""")
with w2:
    st.markdown("""**2) Compute**  
CPM + crash, RFI impacts, cost layers, and comparisons.""")
with w3:
    st.markdown("""**3) Save & export**  
Save runs locally to SQLite and export CSV/JSON/ZIP.""")
