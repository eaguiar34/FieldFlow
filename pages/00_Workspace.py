import streamlit as st
import fieldflow_core as core

core.render_sidebar("Workspace")

st.title("FieldFlow Workspace")
st.caption("One place to run schedule, RFI, and cost workflows without bouncing between pages.")

tab_names = ["Schedule", "RFIs", "Cost"]
default_name = st.session_state.get("__ff_workspace_tab__", "Schedule")
default_idx = tab_names.index(default_name) if default_name in tab_names else 0
sel = st.selectbox("Workspace section", options=tab_names, index=default_idx, key="__ff_workspace_selector__")
st.session_state["__ff_workspace_tab__"] = sel
tab_sched, tab_rfi, tab_cost = st.tabs(tab_names)


with tab_sched:
    st.session_state["__ff_embedded__"] = True
    core.schedule_whatifs_page()
    st.markdown("---")
    core.baseline_variance_page()
    st.session_state["__ff_embedded__"] = False

with tab_rfi:
    st.session_state["__ff_embedded__"] = True
    core.rfi_manager_page()
    st.markdown("---")
    core.rfi_impacts_page()
    st.session_state["__ff_embedded__"] = False

with tab_cost:
    st.session_state["__ff_embedded__"] = True
    core.cost_estimator_page()
    st.markdown("---")
    core.cost_rollups_compare_page()
    st.session_state["__ff_embedded__"] = False
