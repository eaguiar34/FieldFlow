import streamlit as st

import fieldflow_core as core

st.set_page_config(page_title="FieldFlow", page_icon=None, layout="wide")

# Global UI polish
st.markdown(
    """
    <style>
    html, body, [class*="css"] { font-size: 17px; color: #000 !important; }
    .stMarkdown, .stText, .stCaption, .stButton, label, p, span, div { color: #000 !important; }

    /* Sidebar typography */
    [data-testid="stSidebar"] * { font-size: 1.08rem; color: #000 !important; }

    /* Hide Streamlit's default multipage hints if they appear */
    [data-testid="stSidebarNav"] { display: none; }

    /* Make the sidebar feel more like an app nav */
    [data-testid="stSidebar"] { border-right: 1px solid rgba(0,0,0,0.08); }

    .ff-topbar {
        padding: 0.35rem 0.5rem 0.75rem 0.5rem;
        border-bottom: 1px solid rgba(0,0,0,0.08);
        margin-bottom: 1rem;
    }
    .ff-brand {
        display: flex;
        align-items: center;
        gap: 0.6rem;
    }
    .ff-brand-title {
        font-weight: 800;
        font-size: 1.25rem;
        line-height: 1.1;
        margin: 0;
    }
    .ff-brand-sub {
        font-size: 0.85rem;
        opacity: 0.75;
        margin: 0;
    }

    /* Card icon sizing */
    .ff-icon { width: 34px; height: 34px; display: inline-block; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Navigation registry (Streamlit pages are still the backend; we render our own UI)
PAGES = {
    "Home": "pages/00_Home.py",
    "Workspace": "pages/00_Workspace.py",
    "Submittal Checker": "pages/01_Submittal_Checker.py",
    "Schedule What-Ifs": "pages/02_Schedule_What_Ifs.py",
    "RFI Manager": "pages/03_RFI_Manager.py",
    "Saved Results": "pages/06_Saved_Results.py",
    "About": "pages/11_About.py",
    "Settings & Examples": "pages/05_Settings_and_Examples.py",
}

nav = st.navigation(
    [
        st.Page(PAGES["Home"], title="Home"),
        st.Page(PAGES["Workspace"], title="Workspace"),
        st.Page(PAGES["Submittal Checker"], title="Submittal Checker"),
        st.Page(PAGES["Schedule What-Ifs"], title="Schedule What-Ifs"),
        st.Page(PAGES["RFI Manager"], title="RFI Manager"),
        st.Page(PAGES["Saved Results"], title="Saved Results"),
        st.Page(PAGES["About"], title="About"),
        st.Page("pages/12_Feedback.py", title="Leave Feedback"),
        st.Page(PAGES["Settings & Examples"], title="Settings & Examples"),
    ],
    position="hidden",
)


def _switch(page_label: str) -> None:
    st.switch_page(PAGES[page_label])


@st.dialog("Leave feedback")
def feedback_dialog() -> None:
    st.write("Tell me what broke, what felt clunky, or what feature you want next.")
    with st.form("ff_feedback_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name (optional)")
        with col2:
            email = st.text_input("Email (optional)")
        rating = st.slider("How was this experience?", min_value=1, max_value=5, value=4)
        msg = st.text_area("Feedback", height=140, placeholder="What should FieldFlow do better?")
        submitted = st.form_submit_button("Send")

    if submitted:
        core.save_feedback(name=name.strip(), email=email.strip(), rating=int(rating), message=msg.strip(), page=str(st.session_state.get("_ff_active_page", "")))
        st.success("Thanks — saved locally.")


@st.dialog("Request a demo")
def demo_dialog() -> None:
    st.write("Leave your info and you'll have a demo request saved locally (for now).")
    with st.form("ff_demo_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Name")
            company = st.text_input("Company")
            role = st.text_input("Role")
        with col2:
            email = st.text_input("Email")
            phone = st.text_input("Phone (optional)")
        msg = st.text_area("What do you want to see?", height=120, placeholder="Scheduling? RFIs? Submittals? Cost? Integrations?")
        submitted = st.form_submit_button("Submit request", type="primary")

    if submitted:
        if not name.strip() or not email.strip():
            st.error("Name and Email are required.")
            return
        core.save_demo_request(
            name=name.strip(),
            email=email.strip(),
            company=company.strip(),
            role=role.strip(),
            phone=phone.strip(),
            message=msg.strip(),
        )
        st.success("Demo request saved locally.")


# Sidebar (single logo, single FieldFlow label)
with st.sidebar:
    # Big logo block (kept only once)
    try:
        st.image("assets/FieldFlow_logo.png", width=170)
    except Exception:
        pass

    st.markdown("---")

    # Primary navigation (with hover help)
    st.page_link(PAGES["Home"], label="Home", help="Landing page and quick start")
    st.page_link(PAGES["Workspace"], label="Workspace", help="Run Schedule + RFIs + Cost without copy/paste")
    st.page_link(PAGES["Submittal Checker"], label="Submittal Checker", help="Compare spec vs submittal; detect gaps; build a register")
    st.page_link(PAGES["Schedule What-Ifs"], label="Schedule What-Ifs", help="CPM + crash-to-target + constraints")
    st.page_link(PAGES["RFI Manager"], label="RFI Manager", help="Track RFIs and link them to schedule impacts")
    st.page_link(PAGES["Saved Results"], label="Saved Results", help="Browse/export saved schedule + submittal runs")

    st.markdown("---")

    # Secondary links + utilities
    colA, colB = st.columns(2)
    with colA:
        if st.button("Request demo", use_container_width=True):
            demo_dialog()
    with colB:
        if st.button("Feedback", use_container_width=True):
            feedback_dialog()

    # Settings/About near the bottom of the nav group
    st.page_link(PAGES["Settings & Examples"], label="Settings & Examples", help="Templates, configuration notes, feature flags")
    st.page_link(PAGES["About"], label="About", help="What FieldFlow is and what it isn't")

    # Minimal language selector (wires into session state; full translation later)
    lang = st.selectbox("Language", options=["English", "Español"], index=0)
    st.session_state["ff_lang"] = lang

    with st.expander("Calendar settings", expanded=False):
        core.calendar_settings_ui()

    if st.button("Purge session data"):
        core.purge_session_data()
        st.success("Session cleared.")


    st.session_state["ff_sidebar_rendered"] = True

# Top horizontal nav (Procore-ish feel)
with st.container():
    st.markdown('<div class="ff-topbar">', unsafe_allow_html=True)
    c1, c2, c3, c4, c5, c6 = st.columns([2.4, 1.1, 1.4, 1.6, 1.1, 1.2])
    with c1:
        st.markdown(
            '<div class="ff-brand"><div><p class="ff-brand-title">FieldFlow</p><p class="ff-brand-sub">offline-first project controls</p></div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        if st.button("Home", use_container_width=True):
            _switch("Home")
    with c3:
        if st.button("Workspace", use_container_width=True):
            _switch("Workspace")
    with c4:
        if st.button("Submittals", use_container_width=True):
            _switch("Submittal Checker")
    with c5:
        if st.button("Schedule", use_container_width=True):
            _switch("Schedule What-Ifs")
    with c6:
        if st.button("Saved", use_container_width=True):
            _switch("Saved Results")
    st.markdown("</div>", unsafe_allow_html=True)


# Remember current page label for feedback
st.session_state["_ff_active_page"] = st.session_state.get("_stcore_navigation__page", "")

nav.run()
