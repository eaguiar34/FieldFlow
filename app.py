import streamlit as st

st.set_page_config(page_title="FieldFlow", page_icon=None, layout="wide")

st.markdown(
    '''
    <style>
    html, body, [class*="css"] { font-size: 17px; color: #000 !important; }
    .stMarkdown, .stText, .stCaption, .stButton, label, p, span, div { color: #000 !important; }
    [data-testid="stSidebar"] * { font-size: 1.12rem; color: #000 !important; }
    </style>
    ''',
    unsafe_allow_html=True,
)

nav = st.navigation(
    [
        st.Page("pages/00_Home.py", title="Home"),
        st.Page("pages/00_Workspace.py", title="Workspace"),
        st.Page("pages/01_Submittal_Checker.py", title="Submittal Checker"),
        st.Page("pages/02_Schedule_What_Ifs.py", title="Schedule What-Ifs"),
        st.Page("pages/03_RFI_Manager.py", title="RFI Manager"),
        st.Page("pages/06_Saved_Results.py", title="Saved Results"),
        st.Page("pages/11_About.py", title="About"),
        st.Page("pages/12_Feedback.py", title="Leave Feedback"),
        st.Page("pages/05_Settings_and_Examples.py", title="Settings & Examples"),
    ],
    position="hidden",
)

with st.sidebar:
    st.page_link("pages/00_Home.py", label="Home", help="Landing page and quick start.")
    st.page_link("pages/00_Workspace.py", label="Workspace", help="Runs Schedule, RFI, and Cost workflows without copy/paste.")
    st.page_link("pages/01_Submittal_Checker.py", label="Submittal Checker", help="Compare spec vs submittal text; find gaps and generate a register.")
    st.page_link("pages/02_Schedule_What_Ifs.py", label="Schedule What-Ifs", help="Compute CPM, show critical path/float, and crash to a target.")
    st.page_link("pages/03_RFI_Manager.py", label="RFI Manager", help="Track RFIs, aging, and schedule risk impacts.")
    st.page_link("pages/06_Saved_Results.py", label="Saved Results", help="Browse, filter, and export saved checks and schedule runs (CSV/JSON/ZIP).")
    st.markdown("---")
    st.page_link("pages/11_About.py", label="About", help="What FieldFlow is and how it works.")
    st.page_link("pages/12_Feedback.py", label="Leave Feedback", help="Send feedback and feature requests.")
    st.page_link("pages/05_Settings_and_Examples.py", label="Settings & Examples", help="Sample templates and configuration notes.")

nav.run()
