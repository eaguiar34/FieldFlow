import streamlit as st
import fieldflow_core as core

st.set_page_config(page_title="FieldFlow", page_icon="🛠️", layout="wide")

core.render_sidebar("Home")

st.title("FieldFlow")
st.caption("Offline-first scheduling + submittals + RFIs + cost tools. Local SQLite only.")

st.markdown("""
### Suggested flow
1. **Schedule What-Ifs** → compute CPM / crash
2. **RFI Manager** → track RFIs
3. **RFI Impacts** → bind RFIs to schedule and simulate
4. **Cost Estimator** → estimate + save snapshots
5. **Cost Rollups & Compare** → compare scope/schedule cost impacts
6. **Saved Results** → export/archive anything
""")
