import streamlit as st
import fieldflow_core as core

core.render_sidebar("About")

st.title("About FieldFlow")
st.caption("Offline-first scheduling + submittals + RFIs + cost tools. Local SQLite only.")

st.markdown(
    '''
### What FieldFlow is
FieldFlow is a lightweight, offline-first toolkit for construction planning workflows:
- CPM + crash-to-target scheduling
- RFI tracking and schedule impacts (deterministic + risk / Monte Carlo)
- Spec vs submittal checking
- Cost estimation (loaded, unit-cost, and production-based)

### What it is not (yet)
- A full Primavera P6 replacement
- A document management system
- A multi-user enterprise platform

### Why it exists
To make the “mathy” parts of project controls usable without heavy infrastructure.
'''
)

st.markdown("---")
st.markdown("**Slogan ideas**")
st.write("- Plan faster. Explain clearer. Build smarter.")
st.write("- CPM-grade schedule math, without the baggage.")
