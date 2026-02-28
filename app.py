import streamlit as st
import fieldflow_core as core

st.set_page_config(page_title="FieldFlow", page_icon="🛠️", layout="wide")

# Top-left logo (Streamlit 1.32+). Falls back gracefully if not available.
try:
    st.logo("assets/FieldFlow_logo.png")
except Exception:
    pass

# Sidebar (project context, calendar, etc.)
core.render_sidebar("Home")

st.title("FieldFlow")
st.caption("Offline-first scheduling + submittals + RFIs + cost tools. Local SQLite only.")

# Prefer modern navigation (lets us name the sidebar section 'FieldFlow' instead of 'app').
if hasattr(st, "navigation") and hasattr(st, "Page"):
    pages = [
        st.Page("app.py", title="Home", icon="🏠"),
        st.Page("pages/01_Submittal_Checker.py", title="Submittal Checker", icon="📄"),
        st.Page("pages/02_Schedule_What_Ifs.py", title="Schedule What-Ifs", icon="🗓️"),
        st.Page("pages/03_RFI_Manager.py", title="RFI Manager", icon="❓"),
        st.Page("pages/04_Aging_Dashboard.py", title="Aging Dashboard", icon="⏳"),
        st.Page("pages/05_Settings_and_Examples.py", title="Settings & Examples", icon="⚙️"),
        st.Page("pages/06_Saved_Results.py", title="Saved Results", icon="💾"),
        st.Page("pages/07_RFI_Impacts.py", title="RFI Impacts", icon="🧨"),
        st.Page("pages/08_Baseline_Variance.py", title="Baseline Variance", icon="📐"),
        st.Page("pages/09_Cost_Estimator.py", title="Cost Estimator", icon="💵"),
        st.Page("pages/10_Cost_Rollups_Compare.py", title="Cost Rollups Compare", icon="📊"),
    ]
    nav = st.navigation({"FieldFlow": pages})
    nav.run()
else:
    # Fallback for older Streamlit: keep the built-in /pages navigation.
    st.info("Use the left navigation (pages) to open each tool.")

    st.markdown(
        """
### What's here
- Submittal Checker
- Schedule What-Ifs
- RFI Manager
- Aging Dashboard
- Settings & Examples
- Saved Results
- RFI Impacts
- Baseline Variance
- Cost Estimator
- Cost Rollups Compare
"""
    )
