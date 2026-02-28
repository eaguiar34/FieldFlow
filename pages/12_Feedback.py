import streamlit as st
import fieldflow_core as core

core.render_sidebar("Feedback")

st.title("Leave Feedback")
st.caption("Help improve FieldFlow. Feedback is stored locally in SQLite (this app instance only).")

with st.form("ff_feedback"):
    name = st.text_input("Name (optional)")
    email = st.text_input("Email (optional)")
    category = st.selectbox("Category", ["UI/UX", "Scheduling/CPM", "RFIs", "Submittals", "Cost", "Bug", "Other"])
    rating = st.slider("Overall rating", 1, 5, 4)
    message = st.text_area("Feedback", height=140)
    submitted = st.form_submit_button("Submit", width="stretch")

if submitted:
    fid = core.save_feedback(name=name, email=email, category=category, rating=rating, message=message)
    st.success(f"Thanks! Saved as {fid}")

st.markdown("---")
with st.expander("View recent feedback (admin)", expanded=False):
    rows = core.list_feedback(limit=50)
    if not rows:
        st.caption("No feedback yet.")
    else:
        import pandas as pd
        st.dataframe(pd.DataFrame([dict(r) for r in rows]), width="stretch", hide_index=True)
