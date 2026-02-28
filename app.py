import streamlit as st

st.set_page_config(page_title="FieldFlow", page_icon=None, layout="wide")


st.markdown(
    '''
    <style>
    /* Make text black everywhere */
    html, body, [class*="css"]  { font-size: 17px; color: #000 !important; }
    .stMarkdown, .stText, .stCaption, .stButton, label, p, span, div { color: #000 !important; }

    /* Sidebar readability */
    [data-testid="stSidebar"] * { font-size: 1.12rem; color: #000 !important; }
    </style>
    ''',
    unsafe_allow_html=True,
)



if hasattr(st, "navigation") and hasattr(st, "Page"):
    nav = st.navigation(
        {
            "FieldFlow": [
                st.Page("pages/00_Home.py", title="Home"),
                st.Page("pages/00_Workspace.py", title="Workspace"),
                st.Page("pages/06_Saved_Results.py", title="Saved Results"),
            ],
            "Learn & Help": [
                st.Page("pages/11_About.py", title="About"),
                st.Page("pages/12_Feedback.py", title="Leave Feedback"),
                st.Page("pages/05_Settings_and_Examples.py", title="Settings & Examples"),
            ],
        }
    )
    nav.run()
else:
    st.title("FieldFlow")
    st.caption("Your Streamlit version is using the classic /pages navigation in the sidebar.")
