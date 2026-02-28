import streamlit as st

st.set_page_config(page_title="FieldFlow", page_icon="🛠️", layout="wide")

# Top-left logo (Streamlit >=1.32). Keep it large like the old sidebar card.
try:
    st.logo("assets/FieldFlow_logo.png", size="large")
except Exception:
    try:
        st.logo("assets/FieldFlow_logo.png")
    except Exception:
        pass

if hasattr(st, "navigation") and hasattr(st, "Page"):
    nav = st.navigation(
        {
            "FieldFlow": [
                st.Page("pages/00_Home.py", title="Home"),
                st.Page("pages/00_Workspace.py", title="Workspace"),
                st.Page("pages/01_Submittal_Checker.py", title="Submittal Checker"),
                st.Page("pages/06_Saved_Results.py", title="Saved Results"),
            ],
            "Learn & Help": [
                st.Page("pages/11_About.py", title="About"),
                st.Page("pages/12_Feedback.py", title="Leave Feedback"),
                st.Page("pages/05_Settings_and_Examples.py", title="Settings & Examples"),
            ],
            "Advanced": [
                st.Page("pages/02_Schedule_What_Ifs.py", title="Schedule What-Ifs"),
                st.Page("pages/08_Baseline_Variance.py", title="Baseline Variance"),
                st.Page("pages/03_RFI_Manager.py", title="RFI Manager"),
                st.Page("pages/07_RFI_Impacts.py", title="RFI Impacts"),
                st.Page("pages/09_Cost_Estimator.py", title="Cost Estimator"),
                st.Page("pages/10_Cost_Rollups_Compare.py", title="Cost Compare"),
                st.Page("pages/04_Aging_Dashboard.py", title="Aging Dashboard"),
            ],
        }
    )
    nav.run()
else:
    st.title("FieldFlow")
    st.caption("Your browser Streamlit version is using the classic /pages navigation in the sidebar.")
