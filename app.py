import base64
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="FieldFlow", page_icon=None, layout="wide")

# ---------- Global UI styling ----------
def _inject_global_css() -> None:
    # Logo as a fixed header inside the sidebar (above Streamlit nav)
    logo_path = Path("assets/FieldFlow_logo.png")
    logo_b64 = ""
    if logo_path.exists():
        logo_b64 = base64.b64encode(logo_path.read_bytes()).decode("utf-8")

    st.markdown(
        f'''
        <style>
        /* Typography + black text */
        html, body, [class*="css"]  {{ font-size: 17px; color: #000 !important; }}
        .stMarkdown, .stText, .stCaption, .stButton, label, p, span, div {{ color: #000 !important; }}

        /* Sidebar: bigger nav text + leave room for logo header */
        [data-testid="stSidebar"] * {{ font-size: 1.12rem; color: #000 !important; }}
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
            padding-top: 88px;
        }}

        /* Sidebar logo header */
        [data-testid="stSidebar"]::before {{
            content: "";
            position: absolute;
            top: 10px;
            left: 12px;
            width: 210px;
            height: 70px;
            background-image: url("data:image/png;base64,{logo_b64}");
            background-repeat: no-repeat;
            background-size: contain;
        }}

        /* Hide Streamlit's default nav group headers (if any) */
        [data-testid="stSidebar"] h2 {{
            margin-top: 0.2rem;
        }}
        </style>
        ''',
        unsafe_allow_html=True,
    )

_inject_global_css()

# ---------- Navigation ----------
if hasattr(st, "navigation") and hasattr(st, "Page"):
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
        ]
    )
    nav.run()
else:
    st.title("FieldFlow")
    st.caption("Your Streamlit version is using the classic /pages navigation in the sidebar.")
