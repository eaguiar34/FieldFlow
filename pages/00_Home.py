import base64
from pathlib import Path

import streamlit as st

import fieldflow_core as core


def _svg_img(path: str, size_px: int = 34) -> str:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    b64 = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f"<img class='ff-icon' style='width:{size_px}px;height:{size_px}px' src='data:image/svg+xml;base64,{b64}'/>"


def main() -> None:
    core.ensure_session_state()
    core.render_sidebar()

    # Hero
    st.markdown(
        """
        <div style="padding: 22px 22px; border: 1px solid rgba(0,0,0,0.08); border-radius: 14px; background: linear-gradient(135deg, rgba(0,0,0,0.02), rgba(0,0,0,0.00));">
          <div style="display:flex; gap:18px; align-items:flex-start;">
            <div style="flex:1;">
              <div style="font-size: 44px; font-weight: 900; line-height: 1.05;">FieldFlow</div>
              <div style="font-size: 22px; font-weight: 700; margin-top: 8px;">CPM-grade schedule math + RFIs + submittals + cost — local-first.</div>
              <div style="margin-top: 10px; font-size: 16px; opacity: 0.75;">Built for project controls workflows that need clarity and speed — without cloud storage, logins, or heavy infrastructure.</div>
            </div>
            <div style="width: 260px; padding: 14px; border-radius: 12px; border: 1px solid rgba(0,0,0,0.08); background: white;">
              <div style="font-weight:800;">Quick start</div>
              <div style="margin-top: 8px; font-size: 14px; opacity: 0.75;">Open Workspace to run Schedule → RFIs → Cost with shared context.</div>
              <div style="margin-top: 12px; display:flex; gap:10px;">
                <div style="padding: 8px 10px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.10); font-size: 13px;">Offline-first</div>
                <div style="padding: 8px 10px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.10); font-size: 13px;">SQLite</div>
                <div style="padding: 8px 10px; border-radius: 10px; border: 1px solid rgba(0,0,0,0.10); font-size: 13px;">Fast</div>
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")

    # Primary action bar
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("Open Workspace", use_container_width=True, type="primary"):
            st.switch_page("pages/00_Workspace.py")
    with col2:
        if st.button("Saved Results", use_container_width=True):
            st.switch_page("pages/06_Saved_Results.py")
    with col3:
        if st.button("Leave Feedback", use_container_width=True):
            st.switch_page("pages/12_Feedback.py")

    st.markdown("---")

    st.subheader("What you can do here")

    schedule_icon = _svg_img("assets/icons/schedule.svg")
    rfi_icon = _svg_img("assets/icons/rfi.svg")
    sub_icon = _svg_img("assets/icons/submittal.svg")
    cost_icon = _svg_img("assets/icons/cost.svg")

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(f"{schedule_icon}  ", unsafe_allow_html=True)
        st.markdown("**Scheduling**")
        st.caption("Compute CPM, see critical path/float, crash-to-target, and compare scenarios.")
        if st.button("Open Scheduling", key="home_sched", use_container_width=True):
            st.switch_page("pages/02_Schedule_What_Ifs.py")

    with c2:
        st.markdown(f"{rfi_icon}  ", unsafe_allow_html=True)
        st.markdown("**RFIs**")
        st.caption("Track RFIs, run aging, bind to activities, and simulate schedule risk.")
        if st.button("Open RFIs", key="home_rfi", use_container_width=True):
            st.switch_page("pages/03_RFI_Manager.py")

    with c3:
        st.markdown(f"{sub_icon}  ", unsafe_allow_html=True)
        st.markdown("**Submittals**")
        st.caption("Compare spec vs submittal text, detect gaps, generate a register.")
        if st.button("Open Submittals", key="home_sub", use_container_width=True):
            st.switch_page("pages/01_Submittal_Checker.py")

    with c4:
        st.markdown(f"{cost_icon}  ", unsafe_allow_html=True)
        st.markdown("**Cost**")
        st.caption("Loaded cost curves, unit-cost estimating, production-rate labor+equipment.")
        if st.button("Open Cost", key="home_cost", use_container_width=True):
            st.switch_page("pages/00_Workspace.py")

    st.markdown("---")

    st.subheader("A simple workflow")
    w1, w2, w3 = st.columns(3)
    with w1:
        st.markdown("**1) Load data**")
        st.caption("Upload a schedule CSV, enter RFIs, paste spec/submittal text.")
    with w2:
        st.markdown("**2) Compute**")
        st.caption("CPM + crash, RFI impacts, cost layers, and comparisons.")
    with w3:
        st.markdown("**3) Save & export**")
        st.caption("Save runs locally to SQLite and export CSV/JSON/ZIP.")


if __name__ == "__main__":
    main()
