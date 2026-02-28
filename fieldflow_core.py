from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
import uuid
import hashlib
import difflib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

import numpy as np
import pandas as pd
import streamlit as st

def _safe_json(obj: object) -> Dict[str, object]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return json.loads(obj) if isinstance(obj, str) and obj.strip() else {}
    except Exception:
        return {}

def _split_tags(raw: object) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw)
    if not s.strip():
        return []
    parts = [p.strip() for p in re.split(r"[,;\n]+", s) if p.strip()]
    return parts


# -----------------------------
# Calendar utilities (workday-based dates)
# -----------------------------

@dataclass(frozen=True)
class CalendarConfig:
    name: str
    workweek: Set[int]  # 0=Mon ... 6=Sun
    holidays: Set[date]



def _style_conflicts(df: pd.DataFrame) -> "pd.io.formats.style.Styler":
    sty = df.style
    if "Float" in df.columns:
        sty = sty.apply(lambda s: ["background-color: #ffcccc" if (pd.notna(v) and float(v) < 0) else ("background-color: #fff2cc" if (pd.notna(v) and abs(float(v)) < 1e-9) else "") for v in s], subset=["Float"])
    if "Mismatch?" in df.columns:
        sty = sty.apply(lambda s: ["background-color: #fff2cc" if str(v).strip() else "" for v in s], subset=["Mismatch?"])
    if "Scope change?" in df.columns:
        def _sc(v):
            v=str(v)
            if v=="Added": return "background-color: #d9ead3"
            if v=="Removed": return "background-color: #f4cccc"
            return ""
        sty = sty.apply(lambda s: [_sc(v) for v in s], subset=["Scope change?"])
    return sty


def _style_weekly_delta(df: pd.DataFrame, top_n: int = 10, min_abs: float = 0.0) -> "pd.io.formats.style.Styler":
    sty = df.style
    if "Delta" in df.columns:
        deltas = pd.to_numeric(df["Delta"], errors="coerce").fillna(0.0)
        top_idx = deltas.abs()[deltas.abs() >= float(min_abs)].sort_values(ascending=False).head(int(top_n)).index
        def _hl(row):
            if row.name in top_idx:
                return ["background-color: #fff2cc"] * len(row)
            return [""] * len(row)
        sty = sty.apply(_hl, axis=1)
    return sty



def _color_legend(items: List[Tuple[str, str]]) -> None:
    """Render a small color legend."""
    cols = st.columns(len(items))
    for col, (label, color) in zip(cols, items):
        col.markdown(
            f"<div style='padding:8px;border-radius:8px;background:{color};text-align:center;font-size:0.9em'>{label}</div>",
            unsafe_allow_html=True,
        )


def _parse_holidays(raw: str) -> Set[date]:
    out: Set[date] = set()
    if not raw:
        return out
    parts = re.split(r"[\n,;\s]+", str(raw).strip())
    for p in parts:
        if not p:
            continue
        try:
            out.add(datetime.fromisoformat(p).date())
        except Exception:
            try:
                out.add(datetime.strptime(p, "%Y-%m-%d").date())
            except Exception:
                continue
    return out


def _calendar_from_sidebar() -> CalendarConfig:
    """Read calendar settings from st.session_state (set in render_sidebar)."""
    mode = str(st.session_state.get("__ff_calendar_mode__", "5x8 (Mon-Fri)"))
    hol_raw = str(st.session_state.get("__ff_calendar_holidays__", ""))
    holidays = _parse_holidays(hol_raw)

    if mode.startswith("6"):
        workweek = {0, 1, 2, 3, 4, 5}  # Mon-Sat
        name = "6x10 (Mon-Sat)"
    elif mode.startswith("7"):
        workweek = {0, 1, 2, 3, 4, 5, 6}  # every day
        name = "7x24 (Every day)"
    else:
        workweek = {0, 1, 2, 3, 4}  # Mon-Fri
        name = "5x8 (Mon-Fri)"

    return CalendarConfig(name=name, workweek=workweek, holidays=holidays)


def _is_workday(d: date, cal: CalendarConfig) -> bool:
    return (d.weekday() in cal.workweek) and (d not in cal.holidays)


def _workdays_between(start: date, end: date, cal: CalendarConfig) -> int:
    """Count workdays from start to end (end exclusive). If end <= start, returns 0."""
    if end <= start:
        return 0
    cur = start
    n = 0
    while cur < end:
        if _is_workday(cur, cal):
            n += 1
        cur = date.fromordinal(cur.toordinal() + 1)
    return n


def _add_workdays(start: date, n: int, cal: CalendarConfig) -> date:
    """Add n workdays to start (0 => start)."""
    if n <= 0:
        return start
    cur = start
    remaining = int(n)
    while remaining > 0:
        cur = date.fromordinal(cur.toordinal() + 1)
        if _is_workday(cur, cal):
            remaining -= 1
    return cur


# -----------------------------
# UI helpers
# -----------------------------

APP_NAME = "FieldFlow"
DB_PATH = Path(".fieldflow") / "fieldflow.sqlite"
ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_CANDIDATES = [
    ASSETS_DIR / "FieldFlow_logo.png",
    ASSETS_DIR / "FieldFlow_logo.jpg",
    ASSETS_DIR / "logo.png",
]

# -----------------------------
# Cost estimation helpers
# -----------------------------

def _activity_cost_layer1(df: pd.DataFrame) -> pd.Series:
    """Layer 1: activity cost loading from Cost (fixed) or Normal Cost/day * Duration."""
    dfx = _normalize_cols(df).copy()
    fixed = pd.to_numeric(dfx.get("Cost", np.nan), errors="coerce")
    rate = pd.to_numeric(dfx.get("Normal Cost/day", np.nan), errors="coerce")
    dur = pd.to_numeric(dfx.get("Duration", 0), errors="coerce").fillna(0)
    cost = fixed.where(~fixed.isna(), rate * dur)
    cost = cost.fillna(0.0)
    return cost.astype(float)


def _activity_cost_layer2(df: pd.DataFrame, cost_book_df: pd.DataFrame, activity_map_df: pd.DataFrame) -> pd.DataFrame:
    """Layer 2: quantity * unit cost using local cost book + per-activity mapping."""
    sched = _normalize_cols(df).copy()
    sched["TaskID"] = sched["TaskID"].astype(str)
    book = (cost_book_df or pd.DataFrame()).copy()
    amap = (activity_map_df or pd.DataFrame()).copy()

    if not book.empty:
        book["code"] = book.get("code", "").astype(str)
        book["unit"] = book.get("unit", "").astype(str)
        book["unit_cost"] = pd.to_numeric(book.get("unit_cost", np.nan), errors="coerce")
    if not amap.empty:
        amap["task_id"] = amap.get("task_id", "").astype(str)
        amap["cost_code"] = amap.get("cost_code", "").astype(str)

    merged = sched.merge(amap, left_on="TaskID", right_on="task_id", how="left", suffixes=("", "_map"))
    merged = merged.merge(book, left_on="cost_code", right_on="code", how="left", suffixes=("", "_book"))

    qty = pd.to_numeric(merged.get("quantity", merged.get("Quantity", np.nan)), errors="coerce").fillna(0.0)
    ucost = pd.to_numeric(merged.get("unit_cost_override", np.nan), errors="coerce")
    ucost = ucost.where(~ucost.isna(), pd.to_numeric(merged.get("unit_cost", np.nan), errors="coerce"))
    ucost = ucost.where(~ucost.isna(), pd.to_numeric(merged.get("Unit Cost", np.nan), errors="coerce"))
    ext = qty * ucost.fillna(0.0)

    out = pd.DataFrame({
        "TaskID": merged["TaskID"].astype(str),
        "Task": merged.get("Task",""),
        "WBS": merged.get("WBS",""),
        "Area": merged.get("Area",""),
        "Discipline": merged.get("Discipline",""),
        "Cost Code": merged.get("cost_code","").fillna(""),
        "Quantity": qty,
        "Unit": merged.get("unit_map", merged.get("unit","")).fillna(""),
        "Unit Cost": ucost.fillna(0.0),
        "Extended Cost": ext.fillna(0.0),
    })
    return out


def _activity_cost_layer3(df: pd.DataFrame, activity_map_df: pd.DataFrame) -> pd.DataFrame:
    """Layer 3: production-rate driven estimate (labor+equipment), and mismatch flags."""
    sched = _normalize_cols(df).copy()
    sched["TaskID"] = sched["TaskID"].astype(str)
    amap = (activity_map_df or pd.DataFrame()).copy()
    if not amap.empty:
        amap["task_id"] = amap.get("task_id","").astype(str)

    merged = sched.merge(amap, left_on="TaskID", right_on="task_id", how="left", suffixes=("", "_map"))

    qty = pd.to_numeric(merged.get("Quantity", np.nan), errors="coerce").fillna(0.0)
    units_per_day = pd.to_numeric(merged.get("Units/day", np.nan), errors="coerce")
    dur_sched = pd.to_numeric(merged.get("Duration", 0), errors="coerce").fillna(0.0)

    implied_dur = qty / units_per_day
    implied_dur = implied_dur.replace([np.inf, -np.inf], np.nan)

    crew = pd.to_numeric(merged.get("Crew", 0), errors="coerce").fillna(0.0)
    hours_day = pd.to_numeric(merged.get("Hours/day", 8.0), errors="coerce").fillna(8.0)

    labor_rate = pd.to_numeric(merged.get("labor_rate_hr", np.nan), errors="coerce")
    labor_rate = labor_rate.where(~labor_rate.isna(), pd.to_numeric(merged.get("Labor $/hr", np.nan), errors="coerce"))
    labor_rate = labor_rate.fillna(0.0)

    equip_rate = pd.to_numeric(merged.get("equip_rate_day", np.nan), errors="coerce")
    equip_rate = equip_rate.where(~equip_rate.isna(), pd.to_numeric(merged.get("Equip $/day", np.nan), errors="coerce"))
    equip_rate = equip_rate.fillna(0.0)

    labor_hours = crew * hours_day * dur_sched
    labor_cost = labor_hours * labor_rate
    equip_cost = dur_sched * equip_rate

    mismatch = []
    for d_s, d_i in zip(dur_sched.tolist(), implied_dur.tolist()):
        if pd.isna(d_i) or d_i <= 0:
            mismatch.append("")
        else:
            mismatch.append("⚠️" if abs(float(d_s) - float(d_i)) / max(1.0, float(d_i)) > 0.25 else "")

    out = pd.DataFrame({
        "TaskID": merged["TaskID"].astype(str),
        "Task": merged.get("Task",""),
        "Quantity": qty,
        "Units/day": units_per_day.fillna(np.nan),
        "Duration (sched)": dur_sched,
        "Implied duration": implied_dur,
        "Mismatch?": mismatch,
        "Crew": crew,
        "Hours/day": hours_day,
        "Labor $/hr": labor_rate,
        "Labor hours": labor_hours,
        "Labor cost": labor_cost,
        "Equip $/day": equip_rate,
        "Equip cost": equip_cost,
        "Total (L+E)": (labor_cost + equip_cost),
    })
    return out


def _timephased_cost(schedule_cpm: pd.DataFrame, cost_series: pd.Series, project_start: date, cal: CalendarConfig) -> pd.DataFrame:
    """Allocate each activity's cost evenly across its scheduled workdays and bucket by week."""
    if schedule_cpm is None or schedule_cpm.empty:
        return pd.DataFrame(columns=["Week", "Cost"])
    sch = schedule_cpm.copy()
    sch["TaskID"] = sch["TaskID"].astype(str)
    sch["ES"] = pd.to_numeric(sch.get("ES", 0), errors="coerce").fillna(0).astype(int)
    sch["EF"] = pd.to_numeric(sch.get("EF", 0), errors="coerce").fillna(0).astype(int)
    cost_map = dict(zip(sch["TaskID"], pd.to_numeric(cost_series, errors="coerce").fillna(0.0).astype(float)))

    daily_rows = []
    for _, r in sch.iterrows():
        tid = str(r["TaskID"])
        es = int(r["ES"]); ef = int(r["EF"])
        dur = max(0, ef-es)
        if dur <= 0:
            continue
        c = float(cost_map.get(tid, 0.0))
        if c == 0:
            continue
        per_day = c / dur
        for d in range(es, ef):
            dt = _add_workdays(project_start, d, cal)
            # bucket by ISO week Monday
            week = dt.isocalendar().week
            year = dt.isocalendar().year
            daily_rows.append({"Year": year, "Week": week, "Date": dt, "Cost": per_day})

    if not daily_rows:
        return pd.DataFrame(columns=["Year","Week","Cost"])
    df = pd.DataFrame(daily_rows)
    out = df.groupby(["Year","Week"], as_index=False)["Cost"].sum()
    out["WeekLabel"] = out.apply(lambda x: f"{int(x['Year'])}-W{int(x['Week']):02d}", axis=1)
    return out.sort_values(["Year","Week"]).reset_index(drop=True)





def _timephased_delta(cpm_a: pd.DataFrame, cost_a: pd.Series, cpm_b: pd.DataFrame, cost_b: pd.Series, project_start: date, cal: CalendarConfig) -> pd.DataFrame:
    a = _timephased_cost(cpm_a, cost_a, project_start, cal)
    b = _timephased_cost(cpm_b, cost_b, project_start, cal)
    if a.empty and b.empty:
        return pd.DataFrame(columns=["WeekLabel","Cost_A","Cost_B","Delta"])
    a2=a[["WeekLabel","Cost"]].rename(columns={"Cost":"Cost_A"})
    b2=b[["WeekLabel","Cost"]].rename(columns={"Cost":"Cost_B"})
    m=a2.merge(b2, on="WeekLabel", how="outer").fillna(0.0)
    m["Delta"] = m["Cost_B"] - m["Cost_A"]
    return m.sort_values("WeekLabel").reset_index(drop=True)


def _rollup_costs(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """Roll up cost columns by group_col, if present."""
    if df is None or df.empty or group_col not in df.columns:
        return pd.DataFrame(columns=[group_col, "Cost (L1)", "Extended Cost", "Total (L+E)"])
    dfx = df.copy()
    for c in ["Cost (L1)", "Extended Cost", "Total (L+E)"]:
        if c not in dfx.columns:
            dfx[c] = 0.0
        dfx[c] = pd.to_numeric(dfx[c], errors="coerce").fillna(0.0)
    out = dfx.groupby(group_col, as_index=False)[["Cost (L1)", "Extended Cost", "Total (L+E)"]].sum()
    out["Grand Total (best available)"] = out[["Total (L+E)", "Extended Cost", "Cost (L1)"]].max(axis=1)
    return out.sort_values("Grand Total (best available)", ascending=False).reset_index(drop=True)


def _compare_estimate_dfs(a: pd.DataFrame, b: pd.DataFrame, missing_mode: str = 'scope_change') -> pd.DataFrame:
    """Compare two saved estimate CSVs by TaskID and produce deltas."""
    if a is None or b is None or a.empty or b.empty:
        return pd.DataFrame()
    aa = a.copy(); bb = b.copy()
    aa["TaskID"] = aa["TaskID"].astype(str); bb["TaskID"] = bb["TaskID"].astype(str)
    for c in ["Cost (L1)", "Extended Cost", "Total (L+E)"]:
        if c in aa.columns: aa[c] = pd.to_numeric(aa[c], errors="coerce").fillna(0.0)
        else: aa[c]=0.0
        if c in bb.columns: bb[c] = pd.to_numeric(bb[c], errors="coerce").fillna(0.0)
        else: bb[c]=0.0
    merged = aa.merge(bb, on="TaskID", how="outer", suffixes=("_A", "_B"))
    merged["_in_A"] = ~merged.get("Task_A").isna()
    merged["_in_B"] = ~merged.get("Task_B").isna()
    merged["Scope change?"] = np.where(merged["_in_A"] & ~merged["_in_B"], "Removed", np.where(~merged["_in_A"] & merged["_in_B"], "Added", ""))
    mm = str(missing_mode or "scope_change").strip().lower()
    if mm in {"ignore", "ignore_missing"}:
        mask = merged["Scope change?"].isin(["Added","Removed"])
        for c in ["Cost (L1)_A","Cost (L1)_B","Extended Cost_A","Extended Cost_B","Total (L+E)_A","Total (L+E)_B"]:
            if c in merged.columns:
                merged.loc[mask, c] = 0.0
    merged["Task"] = merged.get("Task_B").fillna(merged.get("Task_A"))
    for c in ["WBS","Area","Discipline"]:
        merged[c] = merged.get(f"{c}_B").fillna(merged.get(f"{c}_A")).fillna("")
    merged["Δ Cost (L1)"] = merged["Cost (L1)_B"] - merged["Cost (L1)_A"]
    merged["Δ Extended Cost"] = merged["Extended Cost_B"] - merged["Extended Cost_A"]
    merged["Δ Total (L+E)"] = merged["Total (L+E)_B"] - merged["Total (L+E)_A"]
    merged["Δ Best Available"] = merged[["Δ Total (L+E)","Δ Extended Cost","Δ Cost (L1)"]].max(axis=1)
    return merged


def _ensure_db_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _db() -> sqlite3.Connection:
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH.as_posix(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def log_audit_event(event_type: str, entity_type: str, entity_id: str | None, name: str, details: dict) -> str:
    try:
        conn = _db()
        cur = conn.cursor()
        aid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO audit_events (id, created_at, event_type, entity_type, entity_id, name, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, _utc_now_iso(), str(event_type), str(entity_type), (str(entity_id) if entity_id else None), str(name), json.dumps(details or {}, ensure_ascii=False)),
        )
        conn.commit()
        conn.close()
        return aid
    except Exception:
        return ""



def _init_db() -> None:
    conn = _db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            baseline_csv TEXT NOT NULL,
            crashed_csv TEXT,
            meta_json TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS submittal_checks (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            result_json TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rfis (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            project TEXT,
            subject TEXT,
            discipline TEXT,
            priority TEXT,
            question TEXT,
            response TEXT,
            status TEXT,
            due_date TEXT
        )
        """
    )

    
    # Future-proof tables for what-if sandboxes and RFI-to-schedule binding
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_scenarios (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            notes TEXT,
            tags TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_snapshots (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            scenario_id TEXT,
            kind TEXT,
            schedule_json TEXT,
            metrics_json TEXT,
            critical_chain_json TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rfi_links (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            rfi_id TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            delay_days INTEGER DEFAULT 0,
            delay_p50 REAL DEFAULT NULL,
            delay_p80 REAL DEFAULT NULL,
            rule_type TEXT DEFAULT 'fixed',         -- 'fixed' or 'overdue_factor'
            overdue_factor REAL DEFAULT NULL,       -- e.g. 0.5 => 0.5 days delay per day overdue
            apply_as TEXT DEFAULT 'start_delay'     -- 'start_delay' or 'snet_constraint'
        )
"""
    )

    # Ensure new columns exist (for upgrades on existing DBs)
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN delay_p50 REAL DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN delay_p80 REAL DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN rule_type TEXT DEFAULT 'fixed'")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN overdue_factor REAL DEFAULT NULL")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN apply_as TEXT DEFAULT 'start_delay'")
    except Exception:
        pass
    # RFIs risk fields
    for col_def in [
        ("probability", "REAL DEFAULT NULL"),
        ("impact_days", "REAL DEFAULT NULL"),
        ("risk_notes", "TEXT DEFAULT NULL"),
        ("closed_at", "TEXT DEFAULT NULL"),
    ]:
        try:
            cur.execute(f"ALTER TABLE rfis ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass
    # Cost tables upgrades (no-op if already exists)
    try:
        cur.execute("ALTER TABLE cost_book ADD COLUMN region TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE cost_estimates ADD COLUMN cpm_csv TEXT")
    except Exception:
        pass





    # RFI link distribution type
    try:
        cur.execute("ALTER TABLE rfi_links ADD COLUMN dist_type TEXT DEFAULT 'normal'")
    except Exception:
        pass






    
    # Submittal register (generated from specs or manual)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS submittal_register (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT,
            section TEXT,
            submittal_type TEXT,
            requirement TEXT,
            status TEXT,
            due_date TEXT,
            received_date TEXT,
            notes TEXT
        )
        """
    )

    # Audit events for versioning/history
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            name TEXT,
            details_json TEXT
        )
        """
    )

    
    # Cost book (unit costs) + activity cost mapping + saved estimates
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_book (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            code TEXT,
            description TEXT,
            unit TEXT,
            unit_cost REAL,
            region TEXT,
            notes TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_costs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            task_id TEXT NOT NULL,
            cost_code TEXT,
            quantity REAL,
            unit TEXT,
            unit_cost_override REAL,
            fixed_cost_override REAL,
            labor_rate_hr REAL,
            equip_rate_day REAL,
            hours_per_day REAL,
            notes TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_estimates (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT,
            estimate_json TEXT,
            estimate_csv TEXT,
            cpm_csv TEXT
        )
        """
    )

    
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT,
            email TEXT,
            category TEXT,
            rating INTEGER,
            message TEXT
        )
        """
    )
    conn.commit()
    conn.close()


_init_db()


def render_sidebar(active_page: str) -> None:
    st.sidebar.markdown("## FieldFlow")

    st.sidebar.title(APP_NAME)

    st.sidebar.caption("This build saves locally (no Google/Microsoft login).")

    
    with st.sidebar.expander("Project context", expanded=True):
        st.text_input("Project name", value=st.session_state.get("__ff_project_name__", ""), key="__ff_project_name__")
        st.date_input("Project start date", value=st.session_state.get("__ff_project_start__", date.today()), key="__ff_project_start__")
        st.text_input("Default cost region (optional)", value=st.session_state.get("__ff_cost_region__", ""), key="__ff_cost_region__")
        st.button("Reset session (clears computed results)", key="__ff_reset__", help="Clears computed outputs stored in this browser session")
        if st.session_state.get("__ff_reset__"):
            for k in list(st.session_state.keys()):
                if k.startswith("__") or k.startswith("sched_") or k.startswith("rfi_") or k.startswith("cost_") or k.startswith("var_"):
                    st.session_state.pop(k, None)
            st.rerun()

    with st.sidebar.expander("Calendar settings", expanded=False):
        st.selectbox(
            "Working calendar",
            options=["5x8 (Mon-Fri)", "6x10 (Mon-Sat)", "7x24 (Every day)"],
            key="__ff_calendar_mode__",
        )
        st.text_area(
            "Holiday dates (YYYY-MM-DD, separated by commas/new lines)",
            key="__ff_calendar_holidays__",
            height=100,
            placeholder="2026-01-01\n2026-07-04\n2026-11-26",
        )
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Pages**")
    st.sidebar.write("Use the left nav (Streamlit pages) to switch tools.")

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Page: {active_page}")


# -----------------------------
# Storage API
# -----------------------------

# -----------------------------
# UI kit helpers
# -----------------------------

def ui_step(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)


def ui_kpis(items: List[Tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for c, (k, v) in zip(cols, items):
        c.metric(k, v)


def ui_status(kind: str, msg: str) -> None:
    kind = (kind or "").lower()
    if kind in {"ok", "success"}:
        st.success(msg)
    elif kind in {"warn", "warning"}:
        st.warning(msg)
    elif kind in {"error", "fail"}:
        st.error(msg)
    else:
        st.info(msg)


def ui_save_panel(default_name: str, default_tags: str = "", default_notes: str = "", key_prefix: str = "save") -> Dict[str, str]:
    name = st.text_input("Name", value=default_name, key=f"{key_prefix}_name")
    tags = st.text_input("Tags (comma separated)", value=default_tags, key=f"{key_prefix}_tags")
    notes = st.text_area("Notes", value=default_notes, height=70, key=f"{key_prefix}_notes")
    return {"name": name, "tags": tags, "notes": notes}



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_schedule_run(name: str, baseline_df: pd.DataFrame, crashed_df: Optional[pd.DataFrame], meta: dict) -> str:
    run_id = str(uuid.uuid4())
    conn = _db()
    cur = conn.cursor()

    baseline_csv = baseline_df.to_csv(index=False)
    crashed_csv = crashed_df.to_csv(index=False) if crashed_df is not None else None

    cur.execute(
        """
        INSERT INTO schedule_runs (id, created_at, name, baseline_csv, crashed_csv, meta_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, _utc_now_iso(), name, baseline_csv, crashed_csv, json.dumps(meta)),
    )
    conn.commit()
    conn.close()
    return run_id


def list_schedule_runs() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM schedule_runs ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_schedule_run(run_id: str) -> None:
    conn = _db()
    cur = conn.cursor()
    cur.execute("DELETE FROM schedule_runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()


def save_submittal_check(name: str, payload: dict) -> str:
    check_id = str(uuid.uuid4())
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO submittal_checks (id, created_at, name, result_json)
        VALUES (?, ?, ?, ?)
        """,
        (check_id, _utc_now_iso(), name, json.dumps(payload)),
    )
    conn.commit()
    conn.close()
    return check_id


def list_submittal_checks() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM submittal_checks ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def save_submittal_register(rows: list[dict]) -> list[str]:
    ids: list[str] = []
    if not rows:
        return ids
    conn = _db()
    cur = conn.cursor()
    for r in rows:
        sid = str(uuid.uuid4())
        ids.append(sid)
        cur.execute(
            "INSERT INTO submittal_register (id, created_at, name, section, submittal_type, requirement, status, due_date, received_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, _utc_now_iso(), r.get("name"), r.get("section"), r.get("submittal_type"), r.get("requirement"), r.get("status"), r.get("due_date"), r.get("received_date"), r.get("notes")),
        )
    conn.commit()
    conn.close()
    log_audit_event("save", "submittal_register", None, "bulk_insert", {"count": len(ids)})
    return ids


def list_submittal_register(limit: int = 200) -> list[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM submittal_register ORDER BY created_at DESC LIMIT ?", (int(limit),))
    rows = cur.fetchall()
    conn.close()
    return rows



def delete_submittal_check(check_id: str) -> None:
    conn = _db()
    cur = conn.cursor()
    cur.execute("DELETE FROM submittal_checks WHERE id = ?", (check_id,))
    conn.commit()
    conn.close()


def save_rfi(payload: dict) -> str:
    rfi_id = str(uuid.uuid4())
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO rfis (id, created_at, project, subject, discipline, priority, question, response, status, due_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rfi_id,
            _utc_now_iso(),
            payload.get("project"),
            payload.get("subject"),
            payload.get("discipline"),
            payload.get("priority"),
            payload.get("question"),
            payload.get("response"),
            payload.get("status"),
            payload.get("due_date"),
        ),
    )
    conn.commit()
    conn.close()
    return rfi_id



# -----------------------------
# Cost DB helpers
# -----------------------------

def upsert_cost_book_row(code: str, unit: str, unit_cost: float, description: str = "", region: str = "", notes: str = "") -> str:
    conn = _db()
    cur = conn.cursor()
    # unique key by code+unit+region (best-effort)
    cur.execute("SELECT id FROM cost_book WHERE code = ? AND unit = ? AND COALESCE(region,'') = COALESCE(?, '')", (code, unit, region))
    row = cur.fetchone()
    if row:
        cid = row["id"]
        cur.execute(
            "UPDATE cost_book SET description = ?, unit_cost = ?, notes = ? WHERE id = ?",
            (description, float(unit_cost), notes, cid),
        )
    else:
        cid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO cost_book (id, created_at, code, description, unit, unit_cost, region, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, _utc_now_iso(), code, description, unit, float(unit_cost), region, notes),
        )
    conn.commit()
    conn.close()
    return str(cid)


def list_cost_book() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cost_book ORDER BY code ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_activity_cost(task_id: str, cost_code: str = "", quantity: float = 0.0, unit: str = "", unit_cost_override: Optional[float] = None,
                        fixed_cost_override: Optional[float] = None, labor_rate_hr: Optional[float] = None, equip_rate_day: Optional[float] = None,
                        hours_per_day: Optional[float] = None, notes: str = "") -> str:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM activity_costs WHERE task_id = ?", (str(task_id),))
    row = cur.fetchone()
    if row:
        aid = row["id"]
        cur.execute(
            "UPDATE activity_costs SET cost_code=?, quantity=?, unit=?, unit_cost_override=?, fixed_cost_override=?, labor_rate_hr=?, equip_rate_day=?, hours_per_day=?, notes=? WHERE id=?",
            (cost_code, float(quantity or 0), unit, unit_cost_override, fixed_cost_override, labor_rate_hr, equip_rate_day, hours_per_day, notes, aid),
        )
    else:
        aid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO activity_costs (id, created_at, task_id, cost_code, quantity, unit, unit_cost_override, fixed_cost_override, labor_rate_hr, equip_rate_day, hours_per_day, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, _utc_now_iso(), str(task_id), cost_code, float(quantity or 0), unit, unit_cost_override, fixed_cost_override, labor_rate_hr, equip_rate_day, hours_per_day, notes),
        )
    conn.commit()
    conn.close()
    return str(aid)


def list_activity_costs() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM activity_costs ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def save_cost_estimate(name: str, estimate_df: pd.DataFrame, details: Dict[str, object], cpm_df: Optional[pd.DataFrame] = None) -> str:
    eid = str(uuid.uuid4())
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cost_estimates (id, created_at, name, estimate_json, estimate_csv, cpm_csv) VALUES (?, ?, ?, ?, ?, ?)",
        (eid, _utc_now_iso(), name, json.dumps(details or {}, ensure_ascii=False), estimate_df.to_csv(index=False), (cpm_df.to_csv(index=False) if isinstance(cpm_df, pd.DataFrame) and not cpm_df.empty else None)),
    )
    conn.commit()
    conn.close()
    log_audit_event("save", "cost_estimate", eid, name, {"rows": int(len(estimate_df))})
    return eid



def save_feedback(name: str = "", email: str = "", category: str = "Other", rating: int = 4, message: str = "") -> str:
    fid = str(uuid.uuid4())
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO feedback (id, created_at, name, email, category, rating, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, _utc_now_iso(), name, email, category, int(rating), message),
    )
    conn.commit()
    conn.close()
    log_audit_event("save", "feedback", fid, category, {"rating": int(rating)})
    return fid


def list_feedback(limit: int = 100) -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?", (int(limit),))
    rows = cur.fetchall()
    conn.close()
    return rows

def list_cost_estimates() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cost_estimates ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_cost_estimate(eid: str) -> Optional[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cost_estimates WHERE id = ?", (eid,))
    row = cur.fetchone()
    conn.close()
    return row

def list_rfis() -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rfis ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def update_rfi(rfi_id: str, updates: dict) -> None:
    if not updates:
        return
    cols = []
    vals = []
    for k, v in updates.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(rfi_id)
    conn = _db()
    cur = conn.cursor()
    cur.execute(f"UPDATE rfis SET {', '.join(cols)} WHERE id = ?", tuple(vals))
    conn.commit()
    conn.close()


def delete_rfi(rfi_id: str) -> None:
    conn = _db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rfis WHERE id = ?", (rfi_id,))
    conn.commit()
    conn.close()



# -----------------------------
# RFI ↔ Schedule linking + impact simulation
# -----------------------------

def list_rfi_links(rfi_id: Optional[str] = None) -> List[sqlite3.Row]:
    conn = _db()
    cur = conn.cursor()
    if rfi_id:
        cur.execute("SELECT * FROM rfi_links WHERE rfi_id = ? ORDER BY created_at DESC", (rfi_id,))
    else:
        cur.execute("SELECT * FROM rfi_links ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_rfi_link(rfi_id: str, activity_id: str, delay_days: int, delay_p50: Optional[float] = None, delay_p80: Optional[float] = None, rule_type: str = 'fixed', overdue_factor: Optional[float] = None, apply_as: str = 'start_delay') -> str:
    """Create or update a link between an RFI and an activity."""
    link_id = None
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM rfi_links WHERE rfi_id = ? AND activity_id = ?", (rfi_id, activity_id))
    row = cur.fetchone()
    if row:
        link_id = row["id"]
        cur.execute("UPDATE rfi_links SET delay_days = ?, delay_p50 = ?, delay_p80 = ?, rule_type = ?, overdue_factor = ?, apply_as = ? WHERE id = ?", (int(delay_days), delay_p50, delay_p80, str(rule_type), overdue_factor, str(apply_as), link_id))
    else:
        link_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO rfi_links (id, created_at, rfi_id, activity_id, delay_days, delay_p50, delay_p80, rule_type, overdue_factor, apply_as) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (link_id, _utc_now_iso(), rfi_id, str(activity_id), int(delay_days), delay_p50, delay_p80, str(rule_type), overdue_factor, str(apply_as)),
        )
    conn.commit()
    conn.close()
    return str(link_id)


def delete_rfi_link(link_id: str) -> None:
    conn = _db()
    cur = conn.cursor()
    cur.execute("DELETE FROM rfi_links WHERE id = ?", (link_id,))
    conn.commit()
    conn.close()


def _parse_date_safe(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept YYYY-MM-DD or ISO strings
        if len(s) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", s.strip()):
            return datetime.fromisoformat(s.strip()).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.strip())
    except Exception:
        return None


def _is_rfi_overdue(rfi: dict, today: datetime) -> bool:
    status = str(rfi.get("status") or "").strip().lower()
    if status in {"overdue"}:
        return True
    if status in {"answered", "closed", "resolved"}:
        return False
    due = _parse_date_safe(rfi.get("due_date"))
    if due is None:
        return False
    # Compare dates (treat both as UTC)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due.date() < today.date()


def apply_rfi_impacts(schedule_df: pd.DataFrame, rfis_df: pd.DataFrame, links_df: pd.DataFrame, today: Optional[datetime] = None, mode: str = "deterministic", project_start: Optional[date] = None, calendar: Optional[CalendarConfig] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Apply overdue-RFI delays to a schedule and return (impacted_schedule, impact_log).

    Mechanism:
    - For each overdue RFI, add its linked delay_days to the activity's Start Delay.
    - Start Delay acts as an ES floor in CPM forward pass.
    """
    today = today or datetime.now(timezone.utc)
    calendar = calendar or CalendarConfig(name="5x8 (Mon-Fri)", workweek={0,1,2,3,4}, holidays=set())
    sched = _normalize_cols(schedule_df).copy()

    if rfis_df is None or len(rfis_df) == 0 or links_df is None or len(links_df) == 0 or sched.empty:
        return sched, pd.DataFrame(columns=["rfi_id", "activity_id", "delay_days", "reason"])

    # Normalize ids
    sched["TaskID"] = sched["TaskID"].astype(str)
    rfis = rfis_df.copy()
    rfis["id"] = rfis["id"].astype(str)
    links = links_df.copy()
    links["rfi_id"] = links["rfi_id"].astype(str)
    links["activity_id"] = links["activity_id"].astype(str)
    links["delay_days"] = pd.to_numeric(links["delay_days"], errors="coerce").fillna(0).astype(int)
    links["delay_p50"] = pd.to_numeric(links.get("delay_p50", np.nan), errors="coerce")
    links["delay_p80"] = pd.to_numeric(links.get("delay_p80", np.nan), errors="coerce")
    links["rule_type"] = links.get("rule_type", "fixed").astype(str).fillna("fixed")
    links["overdue_factor"] = pd.to_numeric(links.get("overdue_factor", np.nan), errors="coerce")
    links["apply_as"] = links.get("apply_as", "start_delay").astype(str).fillna("start_delay")

    mode_norm = str(mode or "deterministic").strip().lower()
    if mode_norm in {"p50", "median"}:
        links["_delay_use"] = links["delay_p50"].where(~links["delay_p50"].isna(), links["delay_days"].astype(float)).fillna(0)
    elif mode_norm in {"p80"}:
        links["_delay_use"] = links["delay_p80"].where(~links["delay_p80"].isna(), links["delay_days"].astype(float)).fillna(0)
    else:
        links["_delay_use"] = links["delay_days"].astype(float)
    links["_delay_use"] = pd.to_numeric(links["_delay_use"], errors="coerce").fillna(0)

    # Apply rule_type overlay (optional)
    def _rule_delay(row) -> float:
        rt = str(row.get("rule_type", "fixed")).strip().lower()
        if rt in {"overdue_factor", "overdue", "factor"}:
            rid = str(row.get("rfi_id", ""))
            factor = row.get("overdue_factor")
            try:
                f = float(factor) if pd.notna(factor) else 0.0
            except Exception:
                f = 0.0
            return max(0.0, float(overdue_days.get(rid, 0)) * f)
        return float(row.get("_delay_use", 0.0))

    links["_delay_use"] = links.apply(_rule_delay, axis=1)


    # Determine overdue rfis
    overdue_ids = set()
    for _, r in rfis.iterrows():
        if _is_rfi_overdue(r.to_dict(), today=today):
            overdue_ids.add(str(r["id"]))


    # Days overdue (for rule-based delays)
    overdue_days: Dict[str, int] = {}
    for _, r in rfis.iterrows():
        rid = str(r.get("id", ""))
        if rid not in overdue_ids:
            continue
        due_raw = str(r.get("due_date", "") or "").strip()
        dd = 0
        if due_raw:
            try:
                d = datetime.fromisoformat(due_raw).date()
                dd = max(0, (today.date() - d).days)
            except Exception:
                dd = 0
        overdue_days[rid] = int(dd)

    if not overdue_ids:
        return sched, pd.DataFrame(columns=["rfi_id", "activity_id", "delay_days", "reason"])

    links = links[links["rfi_id"].isin(list(overdue_ids))].copy()
    if links.empty:
        return sched, pd.DataFrame(columns=["rfi_id", "activity_id", "delay_days", "reason"])

    # Sum delay by activity
    delay_by_act = links.groupby("activity_id")["_delay_use"].sum().to_dict()
    sched["Start Delay"] = pd.to_numeric(sched.get("Start Delay", 0), errors="coerce").fillna(0).astype(int)

    # Apply impacts either as Start Delay or as SNET constraint dates
    sched["Start Delay"] = pd.to_numeric(sched.get("Start Delay", 0), errors="coerce").fillna(0).astype(int)

    # start-delay impacts
    start_delay_by_act = {k: v for k, v in delay_by_act.items()}
    sched["Start Delay"] = sched["TaskID"].map(lambda tid: int(round(float(start_delay_by_act.get(str(tid), 0.0))))).fillna(0).astype(int)

    # SNET constraint impacts (if requested)
    if project_start is not None:
        # ensure columns exist
        if "Constraint Type" not in sched.columns:
            sched["Constraint Type"] = ""
        if "Constraint Date" not in sched.columns:
            sched["Constraint Date"] = ""
        # For any link marked apply_as='snet_constraint', set Constraint Type/Date to due_date + delay (workdays)
        for _, row in links.iterrows():
            if str(row.get("apply_as", "start_delay")).strip().lower() != "snet_constraint":
                continue
            rid = str(row.get("rfi_id", ""))
            aid = str(row.get("activity_id", ""))
            due_raw = str(rfis.loc[rfis["id"] == rid, "due_date"].iloc[0] if (rid in set(rfis["id"])) else "")
            try:
                due_date = datetime.fromisoformat(str(due_raw)).date()
            except Exception:
                continue
            d = float(row.get("_delay_use", 0.0))
            snet_date = _add_workdays(due_date, int(round(d)), calendar)
            mask = sched["TaskID"].astype(str) == aid
            if mask.any():
                sched.loc[mask, "Constraint Type"] = "SNET"
                sched.loc[mask, "Constraint Date"] = str(snet_date)


    impact_log = links.copy()
    impact_log["reason"] = f"RFI overdue ({mode_norm})"
    impact_log["delay_applied"] = impact_log["_delay_use"]
    return sched, impact_log[["rfi_id", "activity_id", "delay_days", "delay_p50", "delay_p80", "delay_applied", "reason"]]



def simulate_rfi_impacts_monte_carlo(
    schedule_df: pd.DataFrame,
    rfis_df: pd.DataFrame,
    links_df: pd.DataFrame,
    today: Optional[datetime] = None,
    project_start: Optional[date] = None,
    n_iter: int = 200,
    seed: int = 42,
    calendar: Optional[CalendarConfig] = None,
) -> Tuple[pd.DataFrame, List[int]]:
    """Monte Carlo: sample delay per link using (p50, p80) if present and recompute project duration.

    Sampling model (simple but useful):
    - If p50 and p80 exist: assume Normal(mean=p50, std=(p80-p50)/0.8416), clamp at >=0.
    - Else: use deterministic delay_days.
    """
    rng = np.random.default_rng(seed)
    today = today or datetime.now(timezone.utc)
    calendar = calendar or CalendarConfig(name="5x8 (Mon-Fri)", workweek={0,1,2,3,4}, holidays=set())

    base = _normalize_cols(schedule_df)
    if base.empty or rfis_df is None or len(rfis_df) == 0 or links_df is None or len(links_df) == 0:
        return pd.DataFrame(columns=["p10", "p50", "p80", "p90", "mean"]), []

    rfis = rfis_df.copy()
    rfis["id"] = rfis["id"].astype(str)
    links = links_df.copy()
    links["rfi_id"] = links["rfi_id"].astype(str)
    links["activity_id"] = links["activity_id"].astype(str)
    links["delay_days"] = pd.to_numeric(links.get("delay_days", 0), errors="coerce").fillna(0).astype(float)
    links["delay_p50"] = pd.to_numeric(links.get("delay_p50", np.nan), errors="coerce")
    links["delay_p80"] = pd.to_numeric(links.get("delay_p80", np.nan), errors="coerce")

    overdue_ids = set()
    for _, r in rfis.iterrows():
        if _is_rfi_overdue(r.to_dict(), today=today):
            overdue_ids.add(str(r["id"]))

    links = links[links["rfi_id"].isin(list(overdue_ids))].copy()
    if links.empty:
        return pd.DataFrame(columns=["p10", "p50", "p80", "p90", "mean"]), []

    durations: List[int] = []
    for _ in range(int(max(1, n_iter))):
        # sample delays
        sampled = []
        for _, row in links.iterrows():
            p50 = row.get("delay_p50")
            p80 = row.get("delay_p80")
            det = float(row.get("delay_days", 0))
            if pd.notna(p50) and pd.notna(p80) and float(p80) >= float(p50):
                mu = float(p50)
                std = (float(p80) - float(p50)) / 0.8416 if float(p80) > float(p50) else 0.0
                x = float(rng.normal(mu, std)) if std > 0 else mu
                x = max(0.0, x)
            else:
                x = max(0.0, det)
            sampled.append((str(row["activity_id"]), x))

        delay_by_act = {}
        for aid, x in sampled:
            delay_by_act[aid] = delay_by_act.get(aid, 0.0) + float(x)

        sim = base.copy()
        sim["TaskID"] = sim["TaskID"].astype(str)
        sim["Start Delay"] = sim["TaskID"].map(lambda tid: int(round(float(delay_by_act.get(str(tid), 0.0))))).fillna(0).astype(int)

        try:
            sim_out = _compute_schedule(sim, project_start=project_start, calendar=calendar)
            durations.append(int(sim_out["EF"].max()) if "EF" in sim_out.columns and len(sim_out) else 0)
        except Exception:
            # If uncomputable (cycles etc), record 0 and continue
            durations.append(0)

    if not durations:
        return pd.DataFrame(columns=["p10", "p50", "p80", "p90", "mean"]), durations

    s = pd.Series(durations)
    out = pd.DataFrame(
        [{
            "p10": int(s.quantile(0.10)),
            "p50": int(s.quantile(0.50)),
            "p80": int(s.quantile(0.80)),
            "p90": int(s.quantile(0.90)),
            "mean": float(s.mean()),
        }]
    )
    return out, durations

def rfi_impacts_page() -> None:
    if st.session_state.get("__ff_embedded__"):
        st.subheader("RFI Impacts")
    else:
        st.title("RFI Impacts")
    st.caption("Link RFIs to schedule activities, simulate overdue delays, recompute CPM, and save the impacted scenario locally.")

    project_start = st.date_input("Project start date (used for constraint dates)", value=date.today(), key="proj_start_rfi")
    cal = _calendar_from_sidebar()


    # --- Choose schedule source ---
    st.subheader("1) Choose a schedule")
    colA, colB = st.columns([1, 1])
    with colA:
        upload = st.file_uploader("Upload tasks CSV", type=["csv"], key="rfi_sched_up")
    with colB:
        runs = list_schedule_runs()
        run_choices = {f"{r['created_at'][:19]} — {r['name']}": r["id"] for r in runs}
        pick_run = st.selectbox("…or load from a saved schedule run (baseline)", options=["(none)"] + list(run_choices.keys()), index=0)

    base_raw = None
    if pick_run != "(none)":
        run_id = run_choices.get(pick_run)
        if run_id:
            run = get_schedule_run(run_id)
            if run and run.get("baseline_csv"):
                base_raw = pd.read_csv(io.StringIO(run["baseline_csv"]))
    if base_raw is None:
        base_raw = _load_schedule_csv(upload)
    base = _normalize_cols(base_raw)

    st.subheader("Schedule Table")
    edited = st.data_editor(
        base[[c for c in ["Task", "TaskID", "Duration", "Predecessors", "Start Delay", "Constraint Type", "Constraint Date"] if c in base.columns]],
        width="stretch",
        num_rows="dynamic",
        key="rfi_sched_editor",
    )
    edited = _normalize_cols(edited)

    diag = validate_schedule(edited)
    with st.expander("Schedule Health (diagnostics)", expanded=bool(diag.get("issues"))):
        issues = diag.get("issues", [])
        if not issues:
            st.success("No issues detected.")
        else:
            st.warning(f"Found {len(issues)} issue(s). Fix cycles before simulation.")
            st.dataframe(pd.DataFrame(issues), width="stretch")

    # --- RFIs + linking ---
    ui_step("Step 2 — Link RFIs", "Bind RFIs to activities and define delay rules.")
    mode = st.radio(
        "Delay mode",
        options=["Deterministic (days)", "P50 (median)", "P80 (high confidence)", "Monte Carlo (risk)"],
        index=0,
        horizontal=True,
    )
    mode_key = {"Deterministic (days)": "deterministic", "P50 (median)": "p50", "P80 (high confidence)": "p80", "Monte Carlo (risk)": "mc"}[mode]

    rfis_rows = list_rfis()
    if not rfis_rows:
        st.info("No RFIs found yet. Create RFIs in **RFI Manager** first.")
        return

    rfis_df = pd.DataFrame([dict(r) for r in rfis_rows])
    # Pretty columns
    display_cols = [c for c in ["id", "project", "subject", "discipline", "priority", "status", "due_date", "created_at"] if c in rfis_df.columns]
    st.dataframe(rfis_df[display_cols], width="stretch", hide_index=True)

    today = st.date_input("Today (for overdue evaluation)", value=datetime.now().date(), key="rfi_today")
    today_dt = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    mc_iters = 200
    mc_seed = 42
    if mode_key == "mc":
        cmi1, cmi2 = st.columns([1, 1])
        with cmi1:
            mc_iters = st.number_input("Monte Carlo iterations", min_value=50, max_value=2000, value=200, step=50, key="mc_iters")
        with cmi2:
            mc_seed = st.number_input("Random seed", min_value=0, max_value=999999, value=42, step=1, key="mc_seed")



    # Multi-select RFIs
    rfi_labels = []
    for _, r in rfis_df.iterrows():
        due = str(r.get("due_date") or "")
        subj = str(r.get("subject") or "RFI")
        rfi_labels.append(f"{subj[:40]}… ({r['id'][:8]})  due:{due}")
    label_to_id = dict(zip(rfi_labels, rfis_df["id"].astype(str).tolist()))
    selected_labels = st.multiselect("RFIs to include in simulation", options=rfi_labels, default=rfi_labels[:1] if rfi_labels else [])
    selected_ids = [label_to_id[l] for l in selected_labels if l in label_to_id]

    if not selected_ids:
        st.warning("Select at least one RFI to simulate impacts.")
        return

    task_ids = edited["TaskID"].astype(str).tolist()
    task_label_map = {f"{row.TaskID} — {row.Task}": str(row.TaskID) for row in edited.itertuples()}

    # For each selected RFI, edit links
    all_links = pd.DataFrame([dict(r) for r in list_rfi_links()]) if list_rfi_links() else pd.DataFrame(columns=["id","rfi_id","activity_id","delay_days","created_at"])

    for rid in selected_ids:
        rfi_row = rfis_df[rfis_df["id"].astype(str) == str(rid)].iloc[0].to_dict()
        overdue = _is_rfi_overdue(rfi_row, today_dt)
        status_badge = "🚨 overdue" if overdue else "✅ not overdue"
        with st.expander(f"Link RFI {str(rid)[:8]} — {rfi_row.get('subject','(no subject)')} ({status_badge})", expanded=False):
            existing = all_links[all_links["rfi_id"].astype(str) == str(rid)] if not all_links.empty else pd.DataFrame()
            existing_pairs = set(zip(existing.get("activity_id", pd.Series(dtype=str)).astype(str), existing.get("delay_days", pd.Series(dtype=int)).fillna(0).astype(int)))

            picked = st.multiselect(
                "Activities affected by this RFI",
                options=list(task_label_map.keys()),
                default=[k for k,v in task_label_map.items() if v in set(existing.get("activity_id",[]).astype(str))],
                key=f"rfi_link_pick_{rid}",
            )

            # Per-activity delay input
            delay_inputs = {}
            for lab in picked:
                aid = task_label_map[lab]

                prior_days = 0
                prior_p50 = np.nan
                prior_p80 = np.nan
                if not existing.empty:
                    match = existing[existing["activity_id"].astype(str) == str(aid)]
                    if len(match) > 0:
                        prior_days = int(match.iloc[0].get("delay_days") or 0)
                        prior_p50 = match.iloc[0].get("delay_p50")
                        prior_p80 = match.iloc[0].get("delay_p80")

                c1, c2, c3 = st.columns([1, 1, 1])
                with c1:
                    dd = st.number_input(
                        f"Deterministic days ({aid})",
                        min_value=0,
                        max_value=365,
                        value=int(prior_days),
                        step=1,
                        key=f"rfi_delay_det_{rid}_{aid}",
                    )
                with c2:
                    p50 = st.number_input(
                        f"P50 days ({aid})",
                        min_value=0.0,
                        max_value=365.0,
                        value=float(prior_p50) if pd.notna(prior_p50) else float(prior_days),
                        step=0.5,
                        key=f"rfi_delay_p50_{rid}_{aid}",
                    )
                with c3:
                    p80 = st.number_input(
                        f"P80 days ({aid})",
                        min_value=0.0,
                        max_value=365.0,
                        value=float(prior_p80) if pd.notna(prior_p80) else float(p50),
                        step=0.5,
                        key=f"rfi_delay_p80_{rid}_{aid}",
                    )

                r1, r2 = st.columns([1, 1])
                with r1:
                    rule = st.selectbox(
                        f"Rule ({aid})",
                        options=["fixed", "overdue_factor"],
                        index=0,
                        key=f"rfi_rule_{rid}_{aid}",
                    )
                with r2:
                    apply_as = st.selectbox(
                        f"Apply as ({aid})",
                        options=["start_delay", "snet_constraint"],
                        index=0,
                        key=f"rfi_apply_{rid}_{aid}",
                    )

                factor = None
                if rule == "overdue_factor":
                    factor = st.number_input(
                        f"Days delay per day overdue ({aid})",
                        min_value=0.0,
                        max_value=5.0,
                        value=0.5,
                        step=0.1,
                        key=f"rfi_factor_{rid}_{aid}",
                    )
                delay_inputs[aid] = {"delay_days": int(dd), "delay_p50": float(p50), "delay_p80": float(p80), "rule_type": str(rule), "overdue_factor": (float(factor) if factor is not None else None), "apply_as": str(apply_as)}
            if st.button("Save links for this RFI", key=f"save_links_{rid}"):
                # delete removed links
                if not existing.empty:
                    for _, row in existing.iterrows():
                        if str(row["activity_id"]) not in delay_inputs:
                            delete_rfi_link(str(row["id"]))
                # upsert selected
                for aid, vals in delay_inputs.items():
                    upsert_rfi_link(str(rid), str(aid), int(vals.get("delay_days", 0)), float(vals.get("delay_p50", 0.0)), float(vals.get("delay_p80", 0.0)), str(vals.get("rule_type","fixed")), vals.get("overdue_factor", None), str(vals.get("apply_as","start_delay")))
                st.toast("Links saved.")
                st.rerun()

    # --- Simulation ---
    ui_step("Step 3 — Simulate & Save", "Run impacts, review float erosion, then save/export.")
    critical_mode_label = st.radio(
        "Critical path mode",
        options=["Total Float (Float = 0)", "Longest Path (driver chain)"],
        horizontal=True,
        key="rfi_crit_mode",
    )
    critical_mode = "longest_path" if "Longest" in critical_mode_label else "total_float"

    do_sim = st.button("Simulate impacts", width="stretch")
    if do_sim:
        if diag.get("has_cycle"):
            st.error("Cannot simulate: schedule has a cycle. Fix it first.")
            return

        # Baseline CPM (no RFI delay)
        base0 = edited.copy()
        base0["Start Delay"] = 0
        baseline = _compute_longest_path_critical(_compute_schedule(base0, project_start=project_start, calendar=cal))

        # Impacted schedule
        links_df = pd.DataFrame([dict(r) for r in list_rfi_links()]) if list_rfi_links() else pd.DataFrame(columns=["rfi_id","activity_id","delay_days","delay_p50","delay_p80","rule_type","overdue_factor","apply_as"])
        links_df = links_df[links_df["rfi_id"].astype(str).isin([str(x) for x in selected_ids])] if not links_df.empty else links_df
        if mode_key == "mc":
            dist_df, dur_samples = simulate_rfi_impacts_monte_carlo(
                edited,
                rfis_df,
                links_df,
                today=today_dt,
                project_start=project_start,
                n_iter=int(mc_iters),
                seed=int(mc_seed),
                calendar=cal,
            )
            st.session_state["__rfi_mc_dist__"] = dist_df
            st.session_state["__rfi_mc_samples__"] = dur_samples
            # For detailed schedule display, use a conservative P80 run.
            impacted_sched, impact_log = apply_rfi_impacts(edited, rfis_df, links_df, today=today_dt, mode="p80", project_start=project_start, calendar=cal)
        else:
            impacted_sched, impact_log = apply_rfi_impacts(edited, rfis_df, links_df, today=today_dt, mode=mode_key, project_start=project_start, calendar=cal)
        impacted = _compute_longest_path_critical(_compute_schedule(impacted_sched, project_start=project_start, calendar=cal))

        st.session_state["__rfi_baseline__"] = baseline
        st.session_state["__rfi_impacted__"] = impacted
        st.session_state["__rfi_impact_log__"] = impact_log
        st.session_state["__rfi_selected_ids__"] = selected_ids
        st.session_state["__rfi_mode__"] = critical_mode
        st.toast("Simulation complete.")

    baseline = st.session_state.get("__rfi_baseline__")
    impacted = st.session_state.get("__rfi_impacted__")
    impact_log = st.session_state.get("__rfi_impact_log__")
    if baseline is not None and impacted is not None and not baseline.empty and not impacted.empty:
        st.markdown('---')
        st.subheader("Results")
        show_dates = st.checkbox("Show calendar date columns", value=True, key="rfi_show_dates")
        mc_dist = st.session_state.get("__rfi_mc_dist__")
        if mc_dist is not None and len(mc_dist):
            st.markdown("**Monte Carlo duration distribution (days):**")
            st.dataframe(mc_dist, width="stretch", hide_index=True)

        bdur = int(baseline["EF"].max())
        idur = int(impacted["EF"].max())
        mc_dist = st.session_state.get("__rfi_mc_dist__")
        if mc_dist is not None and len(mc_dist) and "p50" in mc_dist.columns:
            try:
                idur = int(mc_dist.iloc[0]["p50"])
            except Exception:
                pass

        st.info(f"Baseline duration: **{bdur}d** → Impacted duration: **{idur}d**  (Δ {idur - bdur:+d}d)")

        # Show chain
        chain_b = _critical_chain_from_schedule(baseline, mode=critical_mode)
        chain_i = _critical_chain_from_schedule(impacted, mode=critical_mode)
        if chain_b:
            st.markdown("**Baseline critical chain:** " + " → ".join([c["TaskID"] for c in chain_b]))
        if chain_i:
            st.markdown("**Impacted critical chain:** " + " → ".join([c["TaskID"] for c in chain_i]))

        if impact_log is not None and len(impact_log) > 0:
            st.subheader("Applied impacts (overdue RFIs only)")
            st.dataframe(_style_conflicts(impact_log), width="stretch", hide_index=True)
            if impact_log is not None and not impact_log.empty:
                st.caption("Impact log includes rule_type (fixed vs overdue_factor) and apply_as (start_delay vs snet_constraint).")
        else:
            st.success("No overdue RFI impacts were applied (none overdue, or no linked activities).")

        # Float erosion table
        cols = ["TaskID", "Task", "ES", "EF", "LS", "LF", "Float"]
        b = baseline[cols].copy()
        i = impacted[cols].copy()
        merged = b.merge(i, on=["TaskID"], suffixes=("_base", "_imp"), how="outer")
        merged["Float_delta"] = merged["Float_imp"] - merged["Float_base"]
        merged["ES_delta"] = merged["ES_imp"] - merged["ES_base"]
        merged["EF_delta"] = merged["EF_imp"] - merged["EF_base"]
        merged = merged.sort_values(["Float_delta", "EF_delta"], ascending=[True, False])
        st.subheader("Top float erosion (most negative ΔFloat)")
        st.dataframe(merged.head(25), width="stretch")

        st.subheader("Save this simulation")
        name = st.text_input("Run name", value=f"RFI Impact — {datetime.now().strftime('%Y-%m-%d %H:%M')}", key="rfi_run_name")
        if st.button("Save simulation as a run (baseline + impacted)", width="stretch", key="rfi_save_run"):
            meta = {
                "kind": "rfi_impact",
                "rfi_ids": st.session_state.get("__rfi_selected_ids__", []),
                "today": today_dt.date().isoformat(),
            }
            run_id = save_schedule_run(
                name=name,
                baseline_df=baseline,
                crashed_df=impacted,
                meta=meta,
            )
            st.success(f"Saved as run {run_id[:8]}. View it in Saved Results.")


# -----------------------------
# Submittal Checker
# -----------------------------


def _read_text_upload(upload) -> str:
    if upload is None:
        return ""
    data = upload.read()
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""



def _extract_submittal_requirements(spec_text: str) -> List[str]:
    """Heuristic: pull likely submittal requirement lines from specs.

    Looks for headings like 'SUBMITTALS' and then bullet/numbered lines beneath.
    This is intentionally lightweight (no OCR, no PDFs).
    """
    if not spec_text:
        return []
    lines = [ln.strip() for ln in spec_text.splitlines()]
    req = []
    in_sub = False
    for ln in lines:
        up = ln.upper()
        if re.search(r"\bSUBMITTALS\b", up):
            in_sub = True
            continue
        if in_sub and re.match(r"^\d+\.\d+\b", ln) and "SUBMIT" not in up:
            # new section number, likely exiting submittals block
            in_sub = False
        if not in_sub:
            continue
        if re.match(r"^[-•\*]\s+", ln) or re.match(r"^\(?[A-Z]\)?\s*\.", ln) or re.match(r"^\d+\)", ln):
            cleaned = re.sub(r"^[-•\*\s]+", "", ln).strip()
            if cleaned and len(cleaned) > 3:
                req.append(cleaned)
        # also capture lines that look like "Product Data:" / "Shop Drawings:" etc
        if re.match(r"^(PRODUCT DATA|SHOP DRAWINGS|SAMPLES|TEST REPORTS|CERTIFICATES)\b", up):
            req.append(ln)
    # de-dup preserve order
    seen=set()
    out=[]
    for r in req:
        key=r.lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:300]

def _extract_bullets(text: str) -> List[str]:
    # Grab simple bullet-like lines
    lines = [ln.strip() for ln in text.splitlines()]
    out = []
    for ln in lines:
        if not ln:
            continue
        if ln.startswith(("-", "*")):
            out.append(ln.lstrip("-* ").strip())
        elif re.match(r"^[A-Z]\.|^\d+\.", ln):
            out.append(ln)
    return out


def _keyword_set(text: str) -> set:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "per",
        "shall",
        "from",
        "that",
        "this",
        "into",
        "your",
        "are",
        "not",
        "use",
        "useful",
        "submittal",
        "section",
    }
    return {w for w in words if w not in stop}


def submittal_checker_page() -> None:
    st.title("Submittal Checker")
    st.caption("Lightweight checker (no OCR / no external deps). Upload text files or paste content.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Spec Source")
        spec_up = st.file_uploader("Upload spec (TXT)", type=["txt"], key="spec_up")
        spec_txt = st.text_area("Or paste spec text", height=200, key="spec_txt")
    with c2:
        st.subheader("Submittal Source")
        sub_up = st.file_uploader("Upload submittal (TXT)", type=["txt"], key="sub_up")
        sub_txt = st.text_area("Or paste submittal text", height=200, key="sub_txt")

    spec = spec_txt.strip() or _read_text_upload(spec_up)
    subm = sub_txt.strip() or _read_text_upload(sub_up)

    analyze = st.button("Analyze")

    if analyze:
        if not spec or not subm:
            st.error("Provide both spec and submittal text.")
            return

        spec_bul = _extract_bullets(spec)
        sub_bul = _extract_bullets(subm)
        req_lines = _extract_submittal_requirements(spec)

        spec_kw = _keyword_set(spec)
        sub_kw = _keyword_set(subm)

        missing = sorted(list(spec_kw - sub_kw))
        extra = sorted(list(sub_kw - spec_kw))
        overlap = sorted(list(spec_kw & sub_kw))

        st.session_state["__submittal_last__"] = {
            "spec_len": len(spec),
            "submittal_len": len(subm),
            "spec_bullets": spec_bul[:200],
            "submittal_bullets": sub_bul[:200],
            "required_submittals": req_lines,
            "missing_keywords": missing[:200],
            "extra_keywords": extra[:200],
            "overlap_keywords": overlap[:200],
        }

    last = st.session_state.get("__submittal_last__")
    if last:
        st.markdown("---")
        st.subheader("Results")

        m1, m2, m3 = st.columns(3)
        m1.metric("Spec words (approx)", str(max(1, last["spec_len"] // 5)))
        m2.metric("Submittal words (approx)", str(max(1, last["submittal_len"] // 5)))
        m3.metric("Keyword overlap", str(len(last["overlap_keywords"])))

        with st.expander("Spec bullets (detected)"):
            st.write(last["spec_bullets"] or "(none detected)")

        with st.expander("Submittal bullets (detected)"):
            st.write(last["submittal_bullets"] or "(none detected)")

        with st.expander("Required submittals (heuristic from spec)"):
            reqs = last.get("required_submittals", []) or []
            if not reqs:
                st.write("(none detected — check that the spec text includes a SUBMITTALS section)")
            else:
                # Simple coverage check: line considered "covered" if >=2 keywords appear in submittal keyword set
                sub_kw_set = set(last.get("overlap_keywords", [])) | set(last.get("extra_keywords", []))
                rows=[]
                for r in reqs[:200]:
                    kws=[k for k in _keyword_set(r) if k]
                    hits=sum(1 for k in kws if k in sub_kw_set)
                    rows.append({"Requirement": r, "Keywords": ", ".join(kws[:8]), "Covered?": "✅" if hits>=2 else "⚠️"})
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

                dfreq = pd.DataFrame(rows)
                st.download_button("Download requirements CSV", data=dfreq.to_csv(index=False).encode("utf-8"), file_name="required_submittals.csv", mime="text/csv")
                st.download_button("Download requirements JSON", data=dfreq.to_json(orient="records", indent=2).encode("utf-8"), file_name="required_submittals.json", mime="application/json")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Possibly missing in submittal**")
            st.write(last["missing_keywords"][:50] or "(none)")
        with c2:
            st.markdown("**Extra terms in submittal**")
            st.write(last["extra_keywords"][:50] or "(none)")

        st.markdown("---")
        
        st.markdown("---")
        st.subheader("Generate submittal register (from spec heuristics)")
        if st.button("Generate submittal register rows", width="stretch"):
            reqs = last.get("required_submittals", []) if last else []
            rows=[]
            for r in (reqs or [])[:200]:
                rows.append({
                    "name": name,
                    "section": "",
                    "submittal_type": "",
                    "requirement": r,
                    "status": "open",
                    "due_date": "",
                    "received_date": "",
                    "notes": "",
                })
            if rows:
                ids = save_submittal_register(rows)
                st.success(f"Inserted {len(ids)} rows into SQLite submittal_register.")
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.warning("No requirements detected to generate.")
        st.subheader("Save")
        name = st.text_input("Name this submittal check", value=f"Submittal check {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if st.button("Save this result"):
            payload = {
                "created_at": _utc_now_iso(),
                "name": name,
                "result": last,
            }
            cid = save_submittal_check(name=name, payload=payload)
            st.success(f"Saved. ID: {cid}")


        # -----------------------------
        # Schedule What-Ifs (simple CPM + crash)
        # -----------------------------



@dataclass(frozen=True)
class Edge:
    pred: str
    succ: str
    rel: str  # FS/SS/FF/SF
    lag: int = 0


def validate_schedule(df: pd.DataFrame) -> Dict[str, object]:
    """Return diagnostics without raising."""
    dfx = _normalize_cols(df)

    issues: List[Dict[str, str]] = []
    if len(dfx) == 0:
        issues.append({"type": "empty", "detail": "No rows in schedule."})

    # basic column sanity
    bad_dur = dfx[dfx["Duration"] < 0]
    for _, r in bad_dur.iterrows():
        issues.append({"type": "negative_duration", "detail": f"{r['TaskID']} has negative Duration"})

    # predecessor references
    tids = set(dfx["TaskID"].astype(str).tolist())
    missing_preds = []
    for _, row in dfx.iterrows():
        preds = str(row.get("Predecessors", "") or "").strip()
        if not preds:
            continue
        parts = [p.strip() for p in preds.split(",") if p.strip()]
        for p in parts:
            try:
                pid, rel, lag = _parse_pred_token(p)
            except Exception:
                issues.append({"type": "bad_pred_format", "detail": f"{row['TaskID']}: could not parse '{p}'"})
                continue
            if pid not in tids:
                missing_preds.append((row["TaskID"], pid))
    for tid, pid in missing_preds[:200]:
        issues.append({"type": "missing_predecessor", "detail": f"{tid} references missing predecessor {pid}"})
    # Suggestions for missing predecessor IDs (best-effort fuzzy match)
    if missing_preds:
        tid_list = sorted(list(tids))
        for tid, pid in missing_preds[:100]:
            matches = difflib.get_close_matches(pid, tid_list, n=1, cutoff=0.6)
            if matches:
                issues.append({"type": "suggestion", "detail": f"Maybe '{pid}' was meant to be '{matches[0]}' (referenced by {tid})"})



    # cycle detection via toposort
    edges = _edges(dfx)
    order, has_cycle, cycle_nodes = _toposort(dfx["TaskID"].tolist(), edges)
    if has_cycle:
        issues.append({
            "type": "cycle",
            "detail": "Precedence cycle detected (CPM cannot be computed until cycle is removed).",
        })

    return {
        "ok": len([i for i in issues if i["type"] in ("cycle",)]) == 0,
        "issues": issues,
        "has_cycle": has_cycle,
        "cycle_nodes": cycle_nodes,
    }

def _load_schedule_csv(upload) -> pd.DataFrame:
    if upload is None:
        # Load sample
        sample = Path(__file__).parent / "sample_data" / "schedule_sample.csv"
        return pd.read_csv(sample)
    return pd.read_csv(upload)


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize user-provided schedule tables into a consistent schema.

    Never raise for missing columns — create safe defaults instead so the UI
    can show actionable diagnostics rather than crashing.
    """
    out = df.copy()

    # Normalize column names (strip whitespace)
    out = out.rename(columns={c: str(c).strip() for c in out.columns})

    # Ensure required columns exist
    if "Task" not in out.columns:
        out["Task"] = ""
    if "Duration" not in out.columns:
        out["Duration"] = 0

    # Optional columns
    if "Predecessors" not in out.columns:
        out["Predecessors"] = ""

    # Optional start delay (used by RFI impact simulation)
    if "Start Delay" not in out.columns:
        out["Start Delay"] = 0

    # Optional constraints (Start No Earlier Than, etc.)
    if "Constraint Type" not in out.columns:
        out["Constraint Type"] = ""
    if "Constraint Date" not in out.columns:
        out["Constraint Date"] = ""


    # Optional metadata / resource columns
    for c in ["WBS", "Area", "Discipline", "Calendar", "Activity Type", "Crew", "Units", "Units/day", "Quantity", "Cost", "Unit Cost", "Labor $/hr", "Equip $/day", "Hours/day"]:
        if c not in out.columns:
            out[c] = ""
    # Cost / crash columns (kept for backward compatibility)
    for c in ["Normal Cost/day", "Crash Cost/day", "Min Duration"]:
        if c not in out.columns:
            out[c] = np.nan if c != "Predecessors" else ""

    # Coerce types
    out["Task"] = out["Task"].astype(str)
    out["Duration"] = pd.to_numeric(out["Duration"], errors="coerce").fillna(0).astype(int)

    out["Start Delay"] = pd.to_numeric(out["Start Delay"], errors="coerce").fillna(0).astype(int)

    out["Constraint Type"] = out["Constraint Type"].astype(str).fillna("")
    out["Constraint Date"] = out["Constraint Date"].astype(str).fillna("")

    out["WBS"] = out["WBS"].astype(str).fillna("")
    out["Area"] = out["Area"].astype(str).fillna("")
    out["Discipline"] = out["Discipline"].astype(str).fillna("")
    out["Calendar"] = out["Calendar"].astype(str).fillna("")
    out["Activity Type"] = out["Activity Type"].astype(str).fillna("").str.lower()

    out["Crew"] = pd.to_numeric(out["Crew"], errors="coerce").fillna(0.0)
    out["Units"] = pd.to_numeric(out["Units"], errors="coerce").fillna(0.0)
    out["Units/day"] = pd.to_numeric(out["Units/day"], errors="coerce").fillna(np.nan)
    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce").fillna(np.nan)

    out["Cost"] = pd.to_numeric(out["Cost"], errors="coerce").fillna(np.nan)
    out["Unit Cost"] = pd.to_numeric(out["Unit Cost"], errors="coerce").fillna(np.nan)
    out["Labor $/hr"] = pd.to_numeric(out["Labor $/hr"], errors="coerce").fillna(np.nan)
    out["Equip $/day"] = pd.to_numeric(out["Equip $/day"], errors="coerce").fillna(np.nan)
    out["Hours/day"] = pd.to_numeric(out["Hours/day"], errors="coerce").fillna(8.0)

    is_milestone = out["Activity Type"].str.contains("milestone", na=False)
    out.loc[is_milestone, "Duration"] = 0

    out["Min Duration"] = pd.to_numeric(out["Min Duration"], errors="coerce")
    out["Min Duration"] = out["Min Duration"].fillna(out["Duration"]).astype(int)
    out.loc[out["Min Duration"] > out["Duration"], "Min Duration"] = out["Duration"]

    # Interpret Crash Cost/day as incremental cost per day reduced (slope).
    out["Crash Cost/day"] = pd.to_numeric(out["Crash Cost/day"], errors="coerce").fillna(np.inf)
    out["Normal Cost/day"] = pd.to_numeric(out["Normal Cost/day"], errors="coerce")

    # Task IDs:
    # - If TaskID exists, keep it (trimmed)
    # - Else derive from Task string or row index
    if "TaskID" in out.columns:
        out["TaskID"] = out["TaskID"].astype(str).str.strip()
    else:
        def _task_id(t: str, i: int) -> str:
            t = str(t or "").strip()
            if " - " in t:
                return t.split(" - ", 1)[0].strip() or f"T{i+1}"
            if t:
                return t.split()[0].strip()
            return f"T{i+1}"
        out["TaskID"] = [_task_id(t, i) for i, t in enumerate(out["Task"].tolist())]

    # Fill blank TaskID rows safely
    out.loc[out["TaskID"].astype(str).str.strip().eq(""), "TaskID"] = [
        f"T{i+1}" for i in range(len(out))
    ]

    # Deduplicate TaskID deterministically (append _2, _3...)
    seen = {}
    fixed = []
    for tid in out["TaskID"].astype(str).tolist():
        base = tid
        k = seen.get(base, 0) + 1
        seen[base] = k
        fixed.append(base if k == 1 else f"{base}_{k}")
    out["TaskID"] = fixed

    return out



def _parse_pred_token(tok: str) -> Tuple[str, str, int]:
    # Examples:
    # "A - Site Prep FS+0" -> ("A", "FS", 0)
    # "B SS+2" -> ("B", "SS", 2)
    tok = tok.strip()
    if not tok:
        raise ValueError("empty predecessor")

    # Find relationship at end
    m = re.search(r"\b(FS|SS|FF|SF)\s*([\+\-]\s*\d+)?\s*$", tok, flags=re.IGNORECASE)
    rel = "FS"
    lag = 0
    head = tok
    if m:
        rel = m.group(1).upper()
        if m.group(2):
            lag = int(m.group(2).replace(" ", ""))
        head = tok[: m.start()].strip()

    # Extract ID from head
    if " - " in head:
        pid = head.split(" - ", 1)[0].strip()
    else:
        pid = head.split()[0].strip()

    return pid, rel, lag


def _edges(df: pd.DataFrame) -> List[Edge]:
    edges: List[Edge] = []
    tids = set(df["TaskID"].astype(str).tolist())
    for _, row in df.iterrows():
        tid = str(row["TaskID"])
        preds = str(row.get("Predecessors", "") or "").strip()
        if not preds:
            continue
        parts = [p.strip() for p in preds.split(",") if p.strip()]
        for p in parts:
            try:
                pid, rel, lag = _parse_pred_token(p)
            except Exception:
                # Validation will surface this; skip for computation
                continue
            # keep edge even if pid missing so diagnostics can show it, but ignore in calc
            edges.append(Edge(pred=pid, succ=tid, rel=rel, lag=lag))
    return edges



def _toposort(nodes: List[str], edges: List[Edge]) -> Tuple[List[str], bool, List[str]]:
    """Return (topo_order, has_cycle, cycle_nodes)."""
    indeg = {n: 0 for n in nodes}
    adj: Dict[str, List[Edge]] = {n: [] for n in nodes}

    for e in edges:
        u, v = e.pred, e.succ
        if u not in indeg:
            indeg[u] = 0
            adj[u] = []
        if v not in indeg:
            indeg[v] = 0
            adj[v] = []
        adj[u].append(e)
        indeg[v] += 1

    q = [n for n in indeg if indeg[n] == 0]
    out: List[str] = []
    while q:
        n = q.pop(0)
        out.append(n)
        for e in adj.get(n, []):
            v = e.succ
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    has_cycle = len(out) != len(indeg)
    cycle_nodes = [n for n in indeg if n not in out] if has_cycle else []
    # Only return nodes actually in this schedule (ignore external preds)
    out_nodes = [n for n in out if n in nodes]
    if has_cycle:
        # include remaining schedule nodes to keep deterministic output
        for n in cycle_nodes:
            if n in nodes and n not in out_nodes:
                out_nodes.append(n)
    return out_nodes, has_cycle, cycle_nodes



def _compute_schedule(df: pd.DataFrame, project_start: Optional[date] = None, calendar: Optional[CalendarConfig] = None, finish_by: Optional[int] = None) -> pd.DataFrame:
    """Compute CPM dates (ES/EF/LS/LF/Float) with relationship types + lag.

    Notes:
    - Missing predecessor IDs are ignored for math (but should be shown in diagnostics).
    - Cycles are not computable; this will raise a ValueError so the caller can show a friendly message.
    """
    df = _normalize_cols(df).copy()
    calendar = calendar or CalendarConfig(name="5x8 (Mon-Fri)", workweek={0,1,2,3,4}, holidays=set())
    nodes = df["TaskID"].astype(str).tolist()
    dur = dict(zip(df["TaskID"].astype(str), df["Duration"].astype(int)))
    start_delay = dict(zip(df["TaskID"].astype(str), df.get("Start Delay", 0).astype(int)))

    # Constraint floors: currently supports SNET (Start No Earlier Than) via "Constraint Type" and "Constraint Date".
    constraint_floor: Dict[str, int] = {n: 0 for n in nodes}
    constraint_lf_ceiling: Dict[str, int] = {n: 10**9 for n in nodes}
    if project_start is not None and "Constraint Type" in df.columns and "Constraint Date" in df.columns:
        for _, row in df.iterrows():
            tid = str(row.get("TaskID", ""))
            ctype = str(row.get("Constraint Type", "")).strip().upper()
            cdate_raw = str(row.get("Constraint Date", "")).strip()
            if not tid or not ctype or not cdate_raw:
                continue
            if ctype not in {"SNET", "START NO EARLIER THAN", "FNET", "FINISH NO EARLIER THAN", "FNLT", "FINISH NO LATER THAN", "MSO", "MUST START ON", "MFO", "MUST FINISH ON"}:
                continue
            try:
                cdate = datetime.fromisoformat(cdate_raw).date()
            except Exception:
                try:
                    cdate = datetime.strptime(cdate_raw, "%Y-%m-%d").date()
                except Exception:
                    continue
            floor = _workdays_between(project_start, cdate, calendar)
            if ctype in {"SNET", "START NO EARLIER THAN"}:
                if floor > constraint_floor.get(tid, 0):
                    constraint_floor[tid] = int(floor)
            elif ctype in {"FNET", "FINISH NO EARLIER THAN"}:
                # Enforce a minimum finish date by pushing ES floor to (finish_floor - duration)
                es_floor = int(floor - int(dur.get(tid, 0)))
                if es_floor > constraint_floor.get(tid, 0):
                    constraint_floor[tid] = es_floor
            elif ctype in {"FNLT", "FINISH NO LATER THAN"}:
                # Enforce a latest finish for backward pass
                if floor < constraint_lf_ceiling.get(tid, 10**9):
                    constraint_lf_ceiling[tid] = int(floor)

    edges = _edges(df)

    order, has_cycle, _cycle_nodes = _toposort(nodes, edges)
    if has_cycle:
        raise ValueError("Cycle detected in predecessors; CPM cannot be computed until the cycle is removed.")

    es = {n: int(max(0, start_delay.get(n, 0), constraint_floor.get(n, 0))) for n in nodes}
    ef = {n: int(es.get(n, 0) + dur.get(n, 0)) for n in nodes}

    # Track the governing predecessor that sets ES (for explainability + critical chain tracing)
    driver_pred: Dict[str, str] = {n: "" for n in nodes}
    driver_rel: Dict[str, str] = {n: "" for n in nodes}
    driver_lag: Dict[str, int] = {n: 0 for n in nodes}
    driver_note: Dict[str, str] = {n: "" for n in nodes}

    # Forward pass
    tids = set(nodes)
    for n in order:
        best_es = es[n]
        best = (driver_pred[n], driver_rel[n], driver_lag[n], driver_note[n])

        # If this task has an explicit Start Delay (e.g., from an overdue RFI), treat it as a baseline driver.
        if start_delay.get(n, 0) > 0 and not best[0]:
            best = ("", "", int(start_delay.get(n, 0)), f"Start Delay (+{int(start_delay.get(n, 0))}d)")

        for e in edges:
            if e.succ != n:
                continue
            u = e.pred
            if u not in tids:
                continue  # ignore missing predecessor IDs
            cand_es = best_es

            if e.rel == "SS":
                cand_es = es[u] + e.lag
                note = f"{u} (SS{e.lag:+d})"
            elif e.rel == "FS":
                cand_es = ef[u] + e.lag
                note = f"{u} (FS{e.lag:+d})"
            elif e.rel == "FF":
                cand_es = ef[u] + e.lag - dur.get(n, 0)
                note = f"{u} (FF{e.lag:+d})"
            elif e.rel == "SF":
                cand_es = es[u] + e.lag - dur.get(n, 0)
                note = f"{u} (SF{e.lag:+d})"
            else:
                cand_es = ef[u] + e.lag
                note = f"{u} (FS{e.lag:+d})"

            if cand_es > best_es:
                best_es = cand_es
                best = (u, e.rel, int(e.lag), note)

        es[n] = int(best_es)
        ef[n] = int(best_es + dur.get(n, 0))
        driver_pred[n], driver_rel[n], driver_lag[n], driver_note[n] = best

    proj = int(max(ef.values()) if ef else 0)
    if finish_by is not None:
        try:
            proj = int(min(proj, int(finish_by)))
        except Exception:
            pass
    if finish_by is not None:
        try:
            proj = int(min(proj, int(finish_by)))
        except Exception:
            pass

    # Backward pass
    ls = {n: proj - dur.get(n, 0) for n in nodes}
    lf = {n: proj for n in nodes}
    # Apply per-activity latest-finish constraints (FNLT)
    for n in nodes:
        ceiling = int(constraint_lf_ceiling.get(n, 10**9))
        if ceiling < 10**8:
            lf[n] = min(lf[n], ceiling)
            ls[n] = lf[n] - dur.get(n, 0)


    rev = list(reversed(order))
    for n in rev:
        for e in edges:
            if e.pred != n:
                continue
            v = e.succ
            if v not in tids:
                continue
            if e.rel == "SS":
                ls[n] = min(ls[n], ls[v] - e.lag)
                lf[n] = ls[n] + dur.get(n, 0)
            elif e.rel == "FS":
                lf[n] = min(lf[n], ls[v] - e.lag)
                ls[n] = lf[n] - dur.get(n, 0)
            elif e.rel == "FF":
                lf[n] = min(lf[n], lf[v] - e.lag)
                ls[n] = lf[n] - dur.get(n, 0)
            elif e.rel == "SF":
                ls[n] = min(ls[n], lf[v] - e.lag)
                lf[n] = ls[n] + dur.get(n, 0)

    out = df.copy()
    out["ES"] = out["TaskID"].map(es).astype(int)
    out["EF"] = out["TaskID"].map(ef).astype(int)
    out["LS"] = out["TaskID"].map(ls).astype(int)
    out["LF"] = out["TaskID"].map(lf).astype(int)
    out["Float"] = out["LS"] - out["ES"]
    out["Critical_TF"] = out["Float"].fillna(0).astype(float).abs() < 1e-9
    out["Logic driver"] = out["TaskID"].map(driver_note)

    # stash driver info for later use (chain tracing) using columns (safe for CSV)
    out["__driver_pred__"] = out["TaskID"].map(driver_pred)
    out["__driver_rel__"] = out["TaskID"].map(driver_rel)
    out["__driver_lag__"] = out["TaskID"].map(driver_lag)

    
    # Calendar date columns (optional)
    if project_start is not None:
        out["ES Date"] = out["ES"].map(lambda x: _add_workdays(project_start, int(x), calendar)).astype(str)
        out["EF Date"] = out["EF"].map(lambda x: _add_workdays(project_start, int(x), calendar)).astype(str)
        out["LS Date"] = out["LS"].map(lambda x: _add_workdays(project_start, int(x), calendar)).astype(str)
        out["LF Date"] = out["LF"].map(lambda x: _add_workdays(project_start, int(x), calendar)).astype(str)
        out["Calendar"] = calendar.name
        if finish_by is not None:
            out["Project Finish By (days)"] = int(finish_by)
            out["Project Finish By Date"] = str(_add_workdays(project_start, int(finish_by), calendar))

    return out.sort_values(["ES", "TaskID"]).reset_index(drop=True)




def _critical_chain_from_schedule(sch: pd.DataFrame, mode: str = "total_float") -> List[Dict[str, object]]:
    """Return one ordered critical chain as a list of dicts.

    mode:
      - 'total_float': chain of Float==0 tasks (Critical_TF), traced by ES driver links
      - 'longest_path': longest ES-driver chain to the project finish (ignores Float)
    """
    if sch is None or sch.empty:
        return []

    tids = sch["TaskID"].astype(str)

    if mode == "total_float":
        if "Critical_TF" not in sch.columns:
            return []
        pool = sch[sch["Critical_TF"] == True].copy()
        if pool.empty:
            return []
        end_row = pool.sort_values(["EF", "ES"], ascending=[False, True]).iloc[0]
        end_id = str(end_row["TaskID"])
        allowed = set(pool["TaskID"].astype(str).tolist())
    else:
        end_row = sch.sort_values(["EF", "ES"], ascending=[False, True]).iloc[0]
        end_id = str(end_row["TaskID"])
        allowed = set(tids.tolist())

    driver = dict(zip(sch["TaskID"].astype(str), sch["__driver_pred__"].astype(str)))
    name_map = dict(zip(sch["TaskID"].astype(str), sch["Task"].astype(str)))

    visited = set()
    chain_ids = []
    cur = end_id
    while cur and cur not in visited:
        visited.add(cur)
        if cur in allowed:
            chain_ids.append(cur)
        nxt = driver.get(cur, "")
        if not nxt:
            break
        cur = nxt

    chain_ids.reverse()
    out_list: List[Dict[str, object]] = []
    for tid in chain_ids:
        row = sch.loc[sch["TaskID"].astype(str) == tid].iloc[0]
        out_list.append({
            "TaskID": tid,
            "Task": name_map.get(tid, ""),
            "ES": int(row["ES"]),
            "EF": int(row["EF"]),
            "Float": int(row.get("Float", 0)),
        })
    return out_list




def _critical_chains(schedule: pd.DataFrame, mode: str = "total_float") -> List[List[Dict[str, object]]]:
    """Return multiple critical chains (if present) traced by stored driver links.

    This is not an exhaustive path enumeration (which can explode), but it reliably
    finds distinct driver chains ending at project finish among critical tasks.
    """
    if schedule is None or schedule.empty:
        return []
    sch = schedule.copy()
    # choose critical set
    if mode == "longest_path" and "Critical_LP" in sch.columns:
        crit = sch[sch["Critical_LP"] == True].copy()
    else:
        crit = sch[sch["Critical_TF"] == True].copy() if "Critical_TF" in sch.columns else sch.copy()

    if crit.empty or "__driver_pred__" not in sch.columns:
        ch = _critical_chain_from_schedule(sch, mode=mode)
        return [ch] if ch else []

    crit_ids = set(crit["TaskID"].astype(str).tolist())
    # approximate end nodes: critical tasks with EF == project finish (or max EF in critical set)
    finish = int(sch["EF"].max()) if "EF" in sch.columns else int(crit["EF"].max()) if "EF" in crit.columns else 0
    ends = [tid for tid in crit_ids if int(sch.loc[sch["TaskID"].astype(str)==tid, "EF"].max() or 0) == finish]

    chains = []
    seen = set()
    for end in ends:
        chain_ids = []
        cur = str(end)
        guard = 0
        while cur and cur not in chain_ids and guard < 1000:
            guard += 1
            if cur in crit_ids or mode=="longest_path":
                chain_ids.append(cur)
            # follow driver
            row = sch[sch["TaskID"].astype(str)==cur]
            if row.empty:
                break
            pred = str(row.iloc[0].get("__driver_pred__", "") or "")
            if not pred:
                break
            cur = pred
        chain_ids = list(reversed(chain_ids))
        key = tuple(chain_ids)
        if key and key not in seen:
            seen.add(key)
            chain = []
            for tid in chain_ids:
                r = sch[sch["TaskID"].astype(str)==tid].iloc[0].to_dict()
                chain.append({"TaskID": tid, "Task": r.get("Task",""), "ES": int(r.get("ES",0)), "EF": int(r.get("EF",0)), "Float": float(r.get("Float",0))})
            chains.append(chain)
    return chains


def _compute_longest_path_critical(sch: pd.DataFrame) -> pd.DataFrame:
    """Mark Critical_LP based on a longest ES-driver chain to project finish."""
    out = sch.copy()
    out["Critical_LP"] = False
    if out.empty:
        return out
    chain = _critical_chain_from_schedule(out, mode="longest_path")
    chain_ids = {c["TaskID"] for c in chain}
    out.loc[out["TaskID"].astype(str).isin(chain_ids), "Critical_LP"] = True
    return out

    chain = _critical_chain_from_schedule(out, mode="total_float")  # start with driver info
    # The driver chain is already the longest ES-driver chain to the max EF node
    chain_ids = {c["TaskID"] for c in chain}
    out.loc[out["TaskID"].astype(str).isin(chain_ids), "Critical_LP"] = True
    return out

def _crash_to_target(df: pd.DataFrame, target: int, critical_mode: str = "total_float", project_start: Optional[date] = None, calendar: Optional[CalendarConfig] = None) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    """Crash to a target project duration.

    Returns:
      crashed_df: the activity table with updated Duration
      plan_df: per-activity crash plan (days reduced + added cost)
      status: dict with messages and stop reason

    Uses 'Crash Cost/day' as the incremental cost per day reduced.
    """
    df = _normalize_cols(df).copy()
    reductions: Dict[str, int] = {tid: 0 for tid in df["TaskID"].astype(str).tolist()}
    added_cost: Dict[str, float] = {tid: 0.0 for tid in df["TaskID"].astype(str).tolist()}

    iter_log: List[Dict[str, object]] = []
    def _project_duration(dfx: pd.DataFrame) -> int:
        sch = _compute_schedule(dfx, project_start=project_start, calendar=calendar)
        return int(sch["EF"].max() if len(sch) else 0)

    try:
        base_dur = _project_duration(df)
    except Exception as e:
        return df, pd.DataFrame(), {"ok": False, "reason": "invalid_schedule", "message": str(e)}

    if base_dur <= target:
        return df, pd.DataFrame(), {"ok": True, "reason": "already_meets_target", "message": f"Baseline duration ({base_dur}) is already <= target ({target})."}

    max_steps = int(max(0, df["Duration"].sum())) + 500
    steps = 0
    stop_reason = "max_steps"

    while steps < max_steps:
        sch = _compute_schedule(df, project_start=project_start, calendar=calendar)
        sch = _compute_longest_path_critical(sch)

        proj = int(sch["EF"].max() if len(sch) else 0)
        finish_date = None
        if project_start is not None and "EF Date" in sch.columns:
            try:
                # project finish date = max EF Date row
                finish_date = str(sch.loc[sch["EF"].idxmax(), "EF Date"])
            except Exception:
                finish_date = None
        if proj <= target:
            stop_reason = "target_met"
            break

        if critical_mode == "longest_path":
            crit = sch[sch["Critical_LP"] == True].copy()
        else:
            crit = sch[sch["Critical_TF"] == True].copy()

        if crit.empty:
            stop_reason = "no_critical_path"
            break

        cand = crit.merge(
            df[["TaskID", "Duration", "Min Duration", "Crash Cost/day"]],
            on="TaskID",
            how="left",
        )
        cand = cand[cand["Duration"] > cand["Min Duration"]]
        if cand.empty:
            stop_reason = "nothing_to_crash"
            break

        cand = cand.sort_values(["Crash Cost/day", "Duration"], ascending=[True, False])
        pick = str(cand.iloc[0]["TaskID"])
        cost_per_day = float(cand.iloc[0]["Crash Cost/day"]) if np.isfinite(cand.iloc[0]["Crash Cost/day"]) else float("inf")

        # Reduce by 1 day
        cur_d = int(df.loc[df["TaskID"] == pick, "Duration"].iloc[0])
        df.loc[df["TaskID"] == pick, "Duration"] = max(int(df.loc[df["TaskID"] == pick, "Min Duration"].iloc[0]), cur_d - 1)

                # Log this crash step (before/after)
        proj_before = proj
        finish_before = finish_date
        try:
            sch_after = _compute_schedule(df, project_start=project_start, calendar=calendar)
            proj_after = int(sch_after["EF"].max() if len(sch_after) else 0)
            finish_after = None
            if project_start is not None and "EF Date" in sch_after.columns and len(sch_after):
                finish_after = str(sch_after.loc[sch_after["EF"].idxmax(), "EF Date"])
        except Exception:
            proj_after = proj_before
            finish_after = finish_before
        iter_log.append({
            "step": int(steps) + 1,
            "activity": pick,
            "proj_before_days": int(proj_before),
            "proj_after_days": int(proj_after),
            "finish_before": finish_before,
            "finish_after": finish_after,
            "crash_cost_per_day": (cost_per_day if np.isfinite(cost_per_day) else None),
        })

        reductions[pick] += 1
        if np.isfinite(cost_per_day):
            added_cost[pick] += cost_per_day
        steps += 1
        
    plan_rows = []
    for tid, days in reductions.items():
        if days <= 0:
            continue
        plan_rows.append({
            "TaskID": tid,
            "Days crashed": days,
            "Added cost": round(float(added_cost.get(tid, 0.0)), 2),
        })
    plan_df = pd.DataFrame(plan_rows).sort_values(["Added cost", "Days crashed"], ascending=[True, False]) if plan_rows else pd.DataFrame(columns=["TaskID","Days crashed","Added cost"])

    status_msg = {
        "target_met": "Target met.",
        "nothing_to_crash": "Nothing to crash (all critical activities already at minimum duration).",
        "no_critical_path": "No critical path found (check logic).",
        "max_steps": "Stopped due to safety limit (possible bad logic or unreachable target).",
    }.get(stop_reason, stop_reason)

    final_finish = None
    if project_start is not None:
        try:
            sch_final = _compute_schedule(df, project_start=project_start, calendar=calendar)
            if len(sch_final) and "EF Date" in sch_final.columns:
                final_finish = str(sch_final.loc[sch_final["EF"].idxmax(), "EF Date"])
        except Exception:
            final_finish = None

    return df, plan_df, {"ok": True, "reason": stop_reason, "message": status_msg, "iter_log": iter_log, "final_finish": final_finish}



# NOTE: The line above uses a fancy quote if pasted from rich text.
# Fix it defensively:



def schedule_whatifs_page() -> None:
    if st.session_state.get("__ff_embedded__"):
        st.subheader("Schedule What-Ifs")
    else:
        st.title("Schedule What-Ifs")
    st.caption(
        "Upload/edit a schedule, compute CPM on demand, optionally crash to a target duration, then save results to local SQLite."
    )

    project_start = st.date_input("Project start date (used for constraint dates)", value=st.session_state.get("__ff_project_start__", date.today()), key="proj_start_sched")
    use_finish_by = st.checkbox("Enforce must-finish-by date (shows negative float if violated)", value=False, key="use_finish_by")
    finish_by_date = st.date_input("Must finish by date", value=_add_workdays(project_start, 0, cal), key="proj_finish_by") if use_finish_by else None
    cal = _calendar_from_sidebar()

    upload = st.file_uploader("Upload tasks CSV", type=["csv"], key="sched_up")
    base_raw = _load_schedule_csv(upload)
    base = _normalize_cols(base_raw)

    with st.expander("CSV columns & example"):
        st.markdown(
            """
Required columns:
- **Task** (name)
- **Duration** (days)

Optional columns:
- **Predecessors** (comma-separated, e.g. `A FS+0`, `B SS+2`, `C FF-1`)
- **Normal Cost/day**, **Crash Cost/day**, **Min Duration**

A sample CSV is available in **Settings & Examples**.
"""
        )

    ui_step("Step 1 — Input schedule", "Upload or edit tasks. Then compute CPM or crash.")
    edited = st.data_editor(
        base[[c for c in ["Task", "TaskID", "Activity Type", "WBS", "Area", "Discipline", "Calendar", "Duration", "Predecessors", "Constraint Type", "Constraint Date", "Crew", "Units", "Units/day", "Quantity", "Normal Cost/day", "Crash Cost/day", "Min Duration"] if c in base.columns]],
        width="stretch",
        num_rows="dynamic",
        key="sched_editor",
    )
    edited = _normalize_cols(edited)

    diag = validate_schedule(edited)
    with st.expander("Schedule Health (diagnostics)", expanded=bool(diag.get("issues"))):
        issues = diag.get("issues", [])
        if not issues:
            st.success("No issues detected.")
        else:
            st.warning(f"Found {len(issues)} issue(s). Fixing cycles is required for CPM.")
            st.dataframe(pd.DataFrame(issues), width="stretch")

    critical_mode_label = st.radio(
        "Critical path mode",
        options=["Total Float (Float = 0)", "Longest Path (driver chain)"],
        horizontal=True,
    )
    critical_mode = "longest_path" if "Longest" in critical_mode_label else "total_float"
    show_dates = st.checkbox("Show calendar date columns (ES/EF/LS/LF)", value=True, key="sched_show_dates")

    c1, c2 = st.columns([1, 1])
    with c1:
        do_cpm = st.button("Compute CPM", width="stretch")
    with c2:
        clear = st.button("Clear results", width="stretch")

    if clear:
        for k in ["__baseline__", "__baseline_mode__", "__crashed__", "__crash_plan__", "__crash_status__"]:
            st.session_state.pop(k, None)
        st.rerun()

    if do_cpm:
        if diag.get("has_cycle"):
            st.error("CPM cannot be computed: cycle detected in predecessors. Fix the cycle and try again.")
        else:
            try:
                baseline = _compute_schedule(edited, project_start=project_start, calendar=cal, finish_by=(_workdays_between(project_start, finish_by_date, cal) if finish_by_date else None))
                baseline = _compute_longest_path_critical(baseline)
                st.session_state["__baseline__"] = baseline
                st.session_state["__baseline_mode__"] = critical_mode
                st.toast("CPM computed.")
            except Exception as e:
                st.error(f"Could not compute CPM: {e}")

    baseline = st.session_state.get("__baseline__")
    if baseline is not None and not baseline.empty:
        st.markdown("---")
        ui_step("Step 2 — Compute CPM", "Compute baseline CPM and critical chain.")

        proj = int(baseline["EF"].max())
        if "EF Date" in baseline.columns:
            st.success(f"Baseline project duration: {proj} days (finish: {baseline.loc[baseline['EF'].idxmax(), 'EF Date']})")
        else:
            st.success(f"Baseline project duration: {proj} days")

        ui_kpis([
            ("Project duration (days)", str(proj)),
            ("Calendar", str(getattr(cal, "name", ""))),
            ("Critical tasks", str(int((baseline["Float"]==0).sum()) if "Float" in baseline.columns else 0)),
        ])
        chain = _critical_chain_from_schedule(baseline, mode=critical_mode)
        if chain:
            chain_ids = " → ".join([c["TaskID"] for c in chain])
            st.markdown(f"**Ordered critical chain ({'Longest Path' if critical_mode=='longest_path' else 'Total Float'}):** {chain_ids}")
            with st.expander("Show chain with task names"):
                st.dataframe(pd.DataFrame(chain), width="stretch")

            chains = _critical_chains(baseline, mode=critical_mode)
            if len(chains) > 1:
                st.warning(f"Multiple critical chains detected: {len(chains)} (showing top {min(len(chains),3)}).")
                for j, ch in enumerate(chains[:3], start=1):
                    st.markdown(f"**Chain {j}:** " + " → ".join([x["TaskID"] for x in ch]))


        # Tables
        if critical_mode == "longest_path":
            crit_tbl = baseline[baseline["Critical_LP"] == True].copy()
        else:
            crit_tbl = baseline[baseline["Critical_TF"] == True].copy()
        st.markdown("**Critical activities**")
        st.dataframe(crit_tbl[["TaskID","Task","Duration","ES","EF","LS","LF","Float","Logic driver"]], width="stretch")

        st.markdown("**Float table**")
        cols = ["TaskID","Task","Duration","ES","EF","LS","LF","Float","Logic driver"]
        if show_dates and "ES Date" in baseline.columns:
            cols += ["ES Date","EF Date","LS Date","LF Date","Calendar"]
        st.caption("Legend")
        _color_legend([
            ("Negative float (late)", "#ffcccc"),
            ("Critical / zero float", "#fff2cc"),
        ])

        st.dataframe(_style_conflicts(baseline[cols]), width="stretch")

        with st.expander("Full baseline table (debug)"):
            st.dataframe(baseline, width="stretch")

        # Downloads
        
        st.markdown("### Cost snapshot (Layer 1)")
        l1 = _activity_cost_layer1(edited)
        st.metric("Baseline loaded cost (Cost or Normal Cost/day × Duration)", f"{float(l1.sum()):,.2f}")
        if not baseline.empty:
            try:
                tp = _timephased_cost(baseline, l1, project_start, cal)
                with st.expander("Time-phased weekly curve (Layer 1)"):
                    st.dataframe(tp[["WeekLabel","Cost"]], width="stretch", hide_index=True)
            except Exception:
                pass

        st.download_button(
            "Download baseline CSV",
            data=baseline.drop(columns=[c for c in ["__driver_pred__","__driver_rel__","__driver_lag__"] if c in baseline.columns]).to_csv(index=False).encode("utf-8"),
            file_name="schedule_baseline.csv",
            mime="text/csv",
            width="stretch",
        )

        st.markdown("---")
        ui_step("Step 3 — Crash & Save", "Optionally crash to a target, review changes, then save/export.")

        target_kind = st.radio(
            "Target type",
            options=["Project duration (days)", "Project finish date"],
            horizontal=True,
            key="crash_target_kind",
        )
        if target_kind == "Project finish date":
            target_date = st.date_input("Target finish date", value=_add_workdays(project_start, proj, cal), key="crash_target_date")
            target = _workdays_between(project_start, target_date, cal)
            st.caption(f"Target = {target} workdays from project start (calendar: {cal.name}).")
        else:
            target = st.number_input("Target project duration (days)", min_value=0, value=max(0, proj), step=1, key="crash_target_days")

        do_crash = st.button("Crash to target", width="stretch")

        if do_crash:
            if diag.get("has_cycle"):
                st.error("Cannot crash: cycle detected in predecessors.")
            else:
                try:
                    crashed_df, plan_df, status = _crash_to_target(edited, int(target), critical_mode=critical_mode, project_start=project_start, calendar=cal)
                    crashed = _compute_schedule(crashed_df, project_start=project_start, calendar=cal)
                    crashed = _compute_longest_path_critical(crashed)
                    st.session_state["__crashed__"] = crashed
                    st.session_state["__crash_plan__"] = plan_df
                    st.session_state["__crash_status__"] = status
                except Exception as e:
                    st.error(f"Crash failed: {e}")

        crashed = st.session_state.get("__crashed__")
        plan_df = st.session_state.get("__crash_plan__")
        status = st.session_state.get("__crash_status__")

        if status:
            itlog = status.get("iter_log") if isinstance(status, dict) else None
            if itlog:
                df_log = pd.DataFrame(itlog)
                try:
                    total_steps = int(df_log["step"].max())
                except Exception:
                    total_steps = len(df_log)
                start_days = int(df_log.iloc[0].get("proj_before_days", 0)) if len(df_log) else 0
                end_days = int(df_log.iloc[-1].get("proj_after_days", start_days)) if len(df_log) else start_days
                start_finish = df_log.iloc[0].get("finish_before", None) if len(df_log) else None
                end_finish = df_log.iloc[-1].get("finish_after", None) if len(df_log) else None
                total_cost = 0.0
                if "crash_cost_per_day" in df_log.columns:
                    total_cost = float(pd.to_numeric(df_log["crash_cost_per_day"], errors="coerce").fillna(0).sum())
                kpi_rows = [{
                    "Total crash steps": total_steps,
                    "Project days": f"{start_days} → {end_days}  (Δ {end_days-start_days:+d})",
                    "Finish date": f"{start_finish} → {end_finish}" if start_finish or end_finish else "(n/a)",
                    "Added cost (approx)": f"{total_cost:,.2f}",
                }]
                st.dataframe(pd.DataFrame(kpi_rows), width="stretch", hide_index=True)

            with st.expander("Crash iteration log (what changed each step)"):
                st.dataframe(pd.DataFrame(itlog), width="stretch", hide_index=True)
            if status.get("reason") == "already_meets_target":
                st.info(status.get("message", ""))
            elif status.get("reason") != "target_met":
                st.warning(status.get("message", ""))

        if crashed is not None and not crashed.empty:
            proj2 = int(crashed["EF"].max())
            if "EF Date" in crashed.columns:
                st.info(
                    f"Crashed project duration: **{proj2}d** (finish: {crashed.loc[crashed['EF'].idxmax(), 'EF Date']})  (target: {int(target)})"
                )
            else:
                st.info(f"Crashed project duration: **{proj2}d** (target: {int(target)})")

            
            try:
                l1_base = _activity_cost_layer1(edited)
                l1_cr = _activity_cost_layer1(crashed_df)
                st.metric("Loaded cost baseline → crashed (Layer 1)", f"{float(l1_base.sum()):,.2f} → {float(l1_cr.sum()):,.2f}")
            except Exception:
                pass

            if isinstance(plan_df, pd.DataFrame) and not plan_df.empty:
                st.markdown("**Crash plan (minimum-cost heuristic)**")
                st.dataframe(plan_df, width="stretch")
                st.caption(f"Total added cost (from Crash Cost/day slopes): {plan_df['Added cost'].sum():,.2f}")

            st.dataframe(crashed[["TaskID","Task","Duration","ES","EF","LS","LF","Float","Logic driver"]], width="stretch")
            st.download_button(
                "Download crashed CSV",
                data=crashed.drop(columns=[c for c in ["__driver_pred__","__driver_rel__","__driver_lag__"] if c in crashed.columns]).to_csv(index=False).encode("utf-8"),
                file_name="schedule_crashed.csv",
                mime="text/csv",
                width="stretch",
            )

        st.markdown("---")
        st.subheader("Save this run (SQLite)")

        with st.expander("Save"):
            run_name = st.text_input("Run name", value=f"Schedule run {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            tags = st.text_input("Tags (comma separated)", value="", key="sched_run_tags")
            save_baseline_only = st.button("Save baseline only", width="stretch")
            save_both = st.button("Save baseline + crashed (if available)", width="stretch")

            if save_baseline_only:
                meta = {
                    "baseline_duration": int(baseline["EF"].max()),
                    "crashed_duration": None,
                    "critical_mode": critical_mode,
                    "calendar": cal.name,
                    "project_start": str(project_start),
                    "tags": tags,
                }
                rid = save_schedule_run(
                    name=run_name,
                    baseline_csv=baseline.to_csv(index=False),
                    crashed_csv="",
                    meta_json=json.dumps(meta),
                )
                st.success(f"Saved baseline run: {rid}")

            if save_both:

                meta = {
                    "baseline_duration_days": proj,
                    "critical_mode": critical_mode,
                    "target_duration_days": int(target),
                    "crashed": crashed is not None,
                }
                rid = save_schedule_run(run_name, baseline, crashed, meta)
                st.success(f"Saved schedule run. ID: {rid}")

    else:
        st.info("Upload/edit tasks, fix any cycles, then click **Compute CPM**.")

    st.markdown("""

Notes:
- FieldFlow stores saved items in a local SQLite DB at `.fieldflow/fieldflow.sqlite`.
- On Streamlit Cloud, local disk is **not guaranteed** to be permanent across rebuilds.

If you need durable storage later, we can plug in a real database (Postgres/Supabase) without changing the UI much.
""")


# -----------------------------
# Saved Results
# -----------------------------




# -----------------------------
# Export helpers
# -----------------------------


def baseline_variance_page() -> None:
    if st.session_state.get("__ff_embedded__"):
        st.subheader("Baseline Variance")
    else:
        st.title("Baseline Variance")
    st.caption("Compare a baseline schedule to a current schedule and see drift in dates/float. Local-only.")

    project_start = st.date_input("Project start date", value=date.today(), key="var_proj_start")
    cal = _calendar_from_sidebar()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Baseline")
        up_b = st.file_uploader("Upload baseline CSV", type=["csv"], key="var_base_up")
    with c2:
        st.subheader("Current")
        up_c = st.file_uploader("Upload current CSV", type=["csv"], key="var_cur_up")

    base_raw = _load_schedule_csv(up_b)
    cur_raw = _load_schedule_csv(up_c)

    base = _normalize_cols(base_raw)
    cur = _normalize_cols(cur_raw)

    do = st.button("Compute variance", width="stretch")
    if do:
        if base.empty or cur.empty:
            st.error("Upload both baseline and current CSVs.")
            return
        try:
            bsch = _compute_schedule(base, project_start=project_start, calendar=cal)
            csch = _compute_schedule(cur, project_start=project_start, calendar=cal)
        except Exception as e:
            st.error(f"Could not compute CPM: {e}")
            return

        b = bsch.set_index("TaskID")
        c = csch.set_index("TaskID")
        common = sorted(list(set(b.index.astype(str)).intersection(set(c.index.astype(str)))))
        rows = []
        for tid in common:
            rows.append({
                "TaskID": tid,
                "Task": str(c.loc[tid].get("Task","")),
                "ES_base": int(b.loc[tid].get("ES",0)),
                "ES_cur": int(c.loc[tid].get("ES",0)),
                "EF_base": int(b.loc[tid].get("EF",0)),
                "EF_cur": int(c.loc[tid].get("EF",0)),
                "Float_base": float(b.loc[tid].get("Float",0)),
                "Float_cur": float(c.loc[tid].get("Float",0)),
            })
        dfv = pd.DataFrame(rows)
        if not dfv.empty:
            dfv["EF_delta"] = dfv["EF_cur"] - dfv["EF_base"]
            dfv["Float_delta"] = dfv["Float_cur"] - dfv["Float_base"]
            st.subheader("Variance table")
            st.dataframe(dfv.sort_values(["EF_delta","Float_delta"], ascending=[False, True]), width="stretch", hide_index=True)

            st.download_button("Download variance CSV", data=dfv.to_csv(index=False).encode("utf-8"), file_name="baseline_variance.csv", mime="text/csv", width="stretch")

            # Save as schedule run (baseline=baseline, crashed=current)
            with st.expander("Save this variance result"):
                name = st.text_input("Name", value=f"Variance {datetime.now().strftime('%Y-%m-%d %H:%M')}", key="var_name")
                tags = st.text_input("Tags (comma separated)", value="variance", key="var_tags")
                if st.button("Save variance to SQLite", width="stretch"):
                    meta = {"kind": "variance", "calendar": cal.name, "project_start": str(project_start), "tags": tags}
                    rid = save_schedule_run(name=name, baseline_df=bsch, crashed_df=csch, meta=meta)
                    st.success(f"Saved: {rid}")
    

def _slug(s: str) -> str:
    s = (s or '').strip()
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'item'


def _build_all_saved_results_zip() -> bytes:
    """Build a ZIP containing all saved results from the local SQLite DB.

    This is computed only when the user clicks the button on the Saved Results page.
    """
    buf = io.BytesIO()
    created = _utc_now_iso()

    runs = list_schedule_runs()
    checks = list_submittal_checks()
    rfis = list_rfis()

    manifest = {
        'created_at': created,
        'counts': {
            'schedule_runs': len(runs),
            'submittal_checks': len(checks),
            'rfis': len(rfis),
        },
        'notes': 'Export generated by FieldFlow (local-only).',
    }

    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=2))

        # Schedule runs
        for r in runs:
            base = f"schedule_runs/{r['created_at']}_{_slug(r['name'])}_{r['id']}"
            z.writestr(f"{base}/baseline.csv", r['baseline_csv'] or '')
            if r['crashed_csv']:
                z.writestr(f"{base}/crashed.csv", r['crashed_csv'])
            meta = {}
            try:
                meta = json.loads(r['meta_json'] or '{}')
            except Exception:
                meta = {}
            meta_out = {
                'id': r['id'],
                'created_at': r['created_at'],
                'name': r['name'],
                'meta': meta,
            }
            z.writestr(f"{base}/meta.json", json.dumps(meta_out, indent=2))

        # Submittal checks
        for c in checks:
            base = f"submittal_checks/{c['created_at']}_{_slug(c['name'])}_{c['id']}"
            z.writestr(f"{base}/result.json", c['result_json'] or '{}')

        # RFIs
        if rfis:
            df = pd.DataFrame([dict(r) for r in rfis])
            z.writestr('rfis/rfis.csv', df.to_csv(index=False))

    return buf.getvalue()


def cost_estimator_page() -> None:
    if st.session_state.get("__ff_embedded__"):
        st.subheader("Cost Estimator")
    else:
        st.title("Cost Estimator")
    st.caption("Local-only cost estimation: (1) cost loading, (2) quantity × unit cost, (3) production-rate labor+equipment.")

    project_start = st.date_input("Project start date (for time-phasing)", value=st.session_state.get("__ff_project_start__", date.today()), key="cost_proj_start")
    cal = _calendar_from_sidebar()

    ui_step("Step 1 — Input", "Load schedule and optional cost fields.")
    up = st.file_uploader("Upload schedule CSV", type=["csv"], key="cost_sched_up")
    sched_raw = _load_schedule_csv(up)
    sched = _normalize_cols(sched_raw)

    sched = st.data_editor(
        sched[[c for c in ["Task","TaskID","WBS","Area","Discipline","Duration","Predecessors","Quantity","Units","Units/day","Crew","Hours/day","Cost","Unit Cost","Normal Cost/day","Labor $/hr","Equip $/day"] if c in sched.columns]],
        width="stretch",
        num_rows="dynamic",
        key="cost_sched_editor",
    )
    sched = _normalize_cols(sched)

    st.markdown("---")
    ui_step("Step 2 — Cost book", "Maintain local unit costs (SQLite).")
    book_rows = [dict(r) for r in list_cost_book()] if list_cost_book() else []
    book_df = pd.DataFrame(book_rows) if book_rows else pd.DataFrame(columns=["code","description","unit","unit_cost","region","notes"])
    book_df = st.data_editor(
        book_df[[c for c in ["code","description","unit","unit_cost","region","notes"] if c in book_df.columns]],
        width="stretch",
        num_rows="dynamic",
        key="cost_book_editor",
    )

    if st.button("Save cost book to SQLite", width="stretch"):
        for _, r in book_df.iterrows():
            code=str(r.get("code","")).strip()
            unit=str(r.get("unit","")).strip()
            if not code or not unit:
                continue
            upsert_cost_book_row(code=code, unit=unit, unit_cost=float(pd.to_numeric(r.get("unit_cost",0), errors="coerce") or 0), description=str(r.get("description","") or ""), region=str(r.get("region","") or ""), notes=str(r.get("notes","") or ""))
        st.toast("Cost book saved.")

    st.markdown("---")
    ui_step("Step 3 — Overrides", "Optional per-activity mapping and overrides.")
    amap_rows = [dict(r) for r in list_activity_costs()] if list_activity_costs() else []
    amap = pd.DataFrame(amap_rows) if amap_rows else pd.DataFrame(columns=["task_id","cost_code","quantity","unit","unit_cost_override","fixed_cost_override","labor_rate_hr","equip_rate_day","hours_per_day","notes","rule_type"])
    amap = st.data_editor(
        amap[[c for c in ["task_id","cost_code","quantity","unit","unit_cost_override","fixed_cost_override","labor_rate_hr","equip_rate_day","hours_per_day","notes"] if c in amap.columns]],
        width="stretch",
        num_rows="dynamic",
        key="activity_costs_editor",
    )

    if st.button("Save activity cost mappings to SQLite", width="stretch"):
        for _, r in amap.iterrows():
            tid=str(r.get("task_id","")).strip()
            if not tid:
                continue
            upsert_activity_cost(
                task_id=tid,
                cost_code=str(r.get("cost_code","") or ""),
                quantity=float(pd.to_numeric(r.get("quantity",0), errors="coerce") or 0),
                unit=str(r.get("unit","") or ""),
                unit_cost_override=(float(r.get("unit_cost_override")) if pd.notna(r.get("unit_cost_override")) else None),
                fixed_cost_override=(float(r.get("fixed_cost_override")) if pd.notna(r.get("fixed_cost_override")) else None),
                labor_rate_hr=(float(r.get("labor_rate_hr")) if pd.notna(r.get("labor_rate_hr")) else None),
                equip_rate_day=(float(r.get("equip_rate_day")) if pd.notna(r.get("equip_rate_day")) else None),
                hours_per_day=(float(r.get("hours_per_day")) if pd.notna(r.get("hours_per_day")) else None),
                notes=str(r.get("notes","") or ""),
            )
        st.toast("Activity costs saved.")

    st.markdown("---")
    ui_step("Step 4 — Compute & Save", "Compute cost layers, review, then save.")
    do = st.button("Compute all 3 layers", width="stretch")
    if do:
        # Compute CPM for time-phasing (best-effort)
        try:
            cpm = _compute_schedule(sched, project_start=project_start, calendar=cal)
        except Exception:
            cpm = pd.DataFrame()

        # Layer 1
        l1 = _activity_cost_layer1(sched)
        total_l1 = float(l1.sum())

        # Layer 2
        book_live = book_df.copy()
        book_live.rename(columns={"code":"code","unit":"unit","unit_cost":"unit_cost"}, inplace=True)
        l2df = _activity_cost_layer2(sched, book_live, amap)
        total_l2 = float(pd.to_numeric(l2df["Extended Cost"], errors="coerce").fillna(0).sum()) if not l2df.empty else 0.0

        # Layer 3
        l3df = _activity_cost_layer3(sched, amap)
        total_l3 = float(pd.to_numeric(l3df["Total (L+E)"], errors="coerce").fillna(0).sum()) if not l3df.empty else 0.0

        st.subheader("Totals")
        m1,m2,m3 = st.columns(3)
        m1.metric("Layer 1 (loaded) total", f"{total_l1:,.2f}")
        m2.metric("Layer 2 (qty×unit) total", f"{total_l2:,.2f}")
        m3.metric("Layer 3 (prod labor+equip) total", f"{total_l3:,.2f}")

        st.markdown("#### Layer 1: Cost loading by activity")
        out1 = sched[["TaskID","Task","Duration"]].copy()
        out1["Cost (L1)"] = l1.values
        st.dataframe(out1, width="stretch", hide_index=True)

        if not cpm.empty:
            tp = _timephased_cost(cpm, l1, project_start, cal)
            st.markdown("**Time-phased (weekly) cost curve (Layer 1)**")
            st.dataframe(tp[["WeekLabel","Cost"]], width="stretch", hide_index=True)

        st.markdown("#### Layer 2: Quantity × unit cost")
        st.dataframe(l2df, width="stretch", hide_index=True)

        st.markdown("#### Layer 3: Production-rate estimate (labor+equipment) + mismatch flags")
        st.caption("Legend")
        _color_legend([
            ("Mismatch flagged", "#fff2cc"),
        ])

        st.dataframe(_style_conflicts(l3df.sort_values(["Mismatch?","Implied duration"], ascending=[False, False])), width="stretch", hide_index=True)

        st.markdown("---")
        st.subheader("Save estimate (SQLite)")
        est_name = st.text_input("Estimate name", value=f"Cost estimate {datetime.now().strftime('%Y-%m-%d %H:%M')}", key="cost_est_name")
        if st.button("Save this estimate", width="stretch"):
            # Save a compact combined table
            merged = sched[["TaskID","Task","WBS","Area","Discipline","Duration","Quantity","Units","Units/day","Crew","Hours/day"]].copy()
            merged["Cost (L1)"] = l1.values
            if not l2df.empty:
                merged = merged.merge(l2df[["TaskID","Cost Code","Quantity","Unit","Unit Cost","Extended Cost"]], on="TaskID", how="left")
            else:
                merged["Extended Cost"] = np.nan
            if not l3df.empty:
                merged = merged.merge(l3df[["TaskID","Labor hours","Labor cost","Equip cost","Total (L+E)","Implied duration","Mismatch?"]], on="TaskID", how="left")
            else:
                merged["Total (L+E)"] = np.nan

            details = {
                "totals": {"layer1": total_l1, "layer2": total_l2, "layer3": total_l3},
                "calendar": cal.name,
                "project_start": str(project_start),
            }
            eid = save_cost_estimate(est_name, merged, details, cpm_df=cpm)
            st.success(f"Saved: {eid}")

            st.download_button("Download combined estimate CSV", data=merged.to_csv(index=False).encode("utf-8"), file_name="cost_estimate.csv", mime="text/csv", width="stretch")
            st.download_button("Download details JSON", data=json.dumps(details, indent=2).encode("utf-8"), file_name="cost_estimate_details.json", mime="application/json", width="stretch")

def cost_rollups_compare_page() -> None:
    if st.session_state.get("__ff_embedded__"):
        st.subheader("Cost Rollups & Compare")
    else:
        st.title("Cost Rollups & Compare")
    st.caption("Roll up costs by WBS/Area/Discipline, and compare two estimates (scope/schedule impact on cost). Local-only.")

    project_start = st.date_input("Project start date (for time-phasing schedule comparisons)", value=st.session_state.get("__ff_project_start__", date.today()), key="cmp_proj_start")
    cal = _calendar_from_sidebar()

    missing_mode = st.radio(
        "How to treat added/removed TaskIDs in comparisons",
        options=["Count as scope change", "Ignore (compare shared tasks only)"],
        horizontal=True,
        key="cmp_missing_mode",
    )
    missing_key = "scope_change" if "scope" in missing_mode.lower() else "ignore"
    min_week_abs = st.number_input("Highlight weekly deltas above (absolute $)", min_value=0.0, value=0.0, step=1000.0, key="week_delta_thresh")
    st.caption(f"Highlighting up to the top 10 weeks where |Δ| ≥ ${min_week_abs:,.0f}.")
    st.caption("Legend")
    _color_legend([
        ("Top delta weeks", "#fff2cc"),
        ("Added task", "#d9ead3"),
        ("Removed task", "#f4cccc"),
    ])



    st.markdown("### Option A: Use saved estimates")
    ests = list_cost_estimates()
    if not ests:
        st.info("No saved cost estimates yet. Create one in **Cost Estimator**.")
    else:
        labels = {f"{e['created_at'][:19]} — {e['name']}": e["id"] for e in ests}
        pick_a = st.selectbox("Estimate A", options=["(none)"] + list(labels.keys()), index=0, key="cmp_est_a")
        pick_b = st.selectbox("Estimate B", options=["(none)"] + list(labels.keys()), index=min(2, len(labels)), key="cmp_est_b")

        if pick_a != "(none)" and pick_b != "(none)" and labels[pick_a] != labels[pick_b]:
            ea = get_cost_estimate(labels[pick_a])
            eb = get_cost_estimate(labels[pick_b])
            a_df = pd.read_csv(io.StringIO(ea["estimate_csv"])) if ea and ea.get("estimate_csv") else pd.DataFrame()
            b_df = pd.read_csv(io.StringIO(eb["estimate_csv"])) if eb and eb.get("estimate_csv") else pd.DataFrame()

                    # Time-phased delta curve (Layer 1) if CPM computable
        try:
            cpm_a = _compute_schedule(a, project_start=project_start, calendar=cal)
            cpm_b = _compute_schedule(b, project_start=project_start, calendar=cal)
            curve = _timephased_delta(cpm_a, a_cost, cpm_b, b_cost, project_start, cal)
            with st.expander("Weekly time-phased cost delta (Layer 1)", expanded=False):
                st.dataframe(_style_weekly_delta(curve, top_n=10, min_abs=min_week_abs), width="stretch", hide_index=True)
                st.download_button("Download weekly delta CSV", data=curve.to_csv(index=False).encode("utf-8"), file_name="weekly_cost_delta.csv", mime="text/csv", width="stretch")
        except Exception:
            pass

            comp = _compare_estimate_dfs(a_df, b_df, missing_mode=missing_key)
            if comp.empty:
                st.warning("Could not compare these estimates (missing CSV data).")
            else:
                # KPI deltas
                def _tot(df, col):
                    return float(pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0).sum()) if col in df.columns else 0.0
                ka = {"L1": _tot(a_df, "Cost (L1)"), "L2": _tot(a_df, "Extended Cost"), "L3": _tot(a_df, "Total (L+E)")}
                kb = {"L1": _tot(b_df, "Cost (L1)"), "L2": _tot(b_df, "Extended Cost"), "L3": _tot(b_df, "Total (L+E)")}

                st.subheader("Total cost deltas (B - A)")
                c1,c2,c3 = st.columns(3)
                c1.metric("Layer 1 (loaded)", f"{kb['L1']-ka['L1']:,.2f}")
                c2.metric("Layer 2 (qty×unit)", f"{kb['L2']-ka['L2']:,.2f}")
                c3.metric("Layer 3 (labor+equip)", f"{kb['L3']-ka['L3']:,.2f}")

                st.markdown("### What changed by activity")
                show = comp[["TaskID","Task","WBS","Area","Discipline","Δ Cost (L1)","Δ Extended Cost","Δ Total (L+E)","Δ Best Available"]].copy()
                show = show.sort_values("Δ Best Available", ascending=False)
                st.dataframe(show.head(200), width="stretch", hide_index=True)
                st.download_button("Download activity deltas CSV", data=show.to_csv(index=False).encode("utf-8"), file_name="estimate_activity_deltas.csv", mime="text/csv", width="stretch")

                # Weekly time-phased delta (saved estimates, Layer 1) if CPM snapshots exist
                try:
                    if ea and eb and ea.get("cpm_csv") and eb.get("cpm_csv"):
                        cpmA = pd.read_csv(io.StringIO(ea["cpm_csv"]))
                        cpmB = pd.read_csv(io.StringIO(eb["cpm_csv"]))
                        costA = pd.to_numeric(a_df.get("Cost (L1)", 0), errors="coerce").fillna(0.0)
                        costB = pd.to_numeric(b_df.get("Cost (L1)", 0), errors="coerce").fillna(0.0)
                        curve = _timephased_delta(cpmA, costA, cpmB, costB, project_start, cal)
                        st.markdown("### Weekly time-phased delta (saved estimates, Layer 1)")
                        st.dataframe(_style_weekly_delta(curve, top_n=10, min_abs=min_week_abs), width="stretch", hide_index=True)
                        top10 = curve.assign(AbsDelta=pd.to_numeric(curve["Delta"], errors="coerce").fillna(0).abs()).sort_values("AbsDelta", ascending=False).head(10)
                        st.markdown("**Top 10 weeks with biggest delta**")
                        st.dataframe(top10[["WeekLabel","Cost_A","Cost_B","Delta","AbsDelta"]], width="stretch", hide_index=True)
                        st.download_button("Download weekly delta CSV (saved estimates)", data=curve.to_csv(index=False).encode("utf-8"), file_name="weekly_cost_delta_saved_estimates.csv", mime="text/csv", width="stretch")
                    else:
                        st.caption("Time-phased delta for saved estimates requires CPM snapshots (newly saved estimates include them).")
                except Exception:
                    pass


                st.markdown("### Rollups (Δ Best Available)")
                for grp in ["WBS","Area","Discipline"]:
                    r = comp.copy()
                    if grp in r.columns:
                        roll = r.groupby(grp, as_index=False)["Δ Best Available"].sum().sort_values("Δ Best Available", ascending=False)
                        with st.expander(f"{grp} rollup", expanded=False):
                            st.dataframe(roll.head(200), width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown("### Option B: Compare two schedules right now (without saving estimates first)")
    st.caption("Upload two schedules, compute Layer 1 costs from each, and see how scope/schedule changes affect costs.")
    up1, up2 = st.columns(2)
    with up1:
        a_up = st.file_uploader("Schedule A CSV", type=["csv"], key="schA_up")
    with up2:
        b_up = st.file_uploader("Schedule B CSV", type=["csv"], key="schB_up")

    if st.button("Compare schedules (Layer 1 cost impact)", width="stretch"):
        a_raw = _load_schedule_csv(a_up)
        b_raw = _load_schedule_csv(b_up)
        a = _normalize_cols(a_raw)
        b = _normalize_cols(b_raw)
        if a.empty or b.empty:
            st.error("Upload both schedule files.")
        else:
            a_cost = _activity_cost_layer1(a)
            b_cost = _activity_cost_layer1(b)
            a_df = a[["TaskID","Task","WBS","Area","Discipline","Duration"]].copy()
            b_df = b[["TaskID","Task","WBS","Area","Discipline","Duration"]].copy()
            a_df["Cost (L1)"] = a_cost.values
            b_df["Cost (L1)"] = b_cost.values
            comp = _compare_estimate_dfs(a_df, b_df, missing_mode=missing_key)
            st.subheader("Total Layer 1 delta (B - A)")
            st.metric("Δ loaded cost", f"{float(comp['Δ Cost (L1)'].sum()):,.2f}")
            st.dataframe(comp[["TaskID","Task","WBS","Area","Discipline","Δ Cost (L1)","Δ Best Available"]].sort_values("Δ Cost (L1)", ascending=False).head(200), width="stretch", hide_index=True)
def saved_results_page() -> None:
    st.title("Saved Results")
    st.caption("Quick links")
    c1,c2,c3 = st.columns(3)
    with c1:
        st.page_link("pages/02_Schedule_What_Ifs.py", label="Go to Schedule What-Ifs")
    with c2:
        st.page_link("pages/09_Cost_Estimator.py", label="Go to Cost Estimator")
    with c3:
        st.page_link("pages/10_Cost_Rollups_Compare.py", label="Go to Cost Compare")
    st.markdown("---")


    # Compute a lightweight signature of DB content to invalidate cached ZIPs
    try:
        runs = list_schedule_runs()
        checks = list_submittal_checks()
        rfis = list_rfis()
        sig = {
            "runs_n": len(runs),
            "checks_n": len(checks),
            "rfis_n": len(rfis),
            "runs_max": max([r["created_at"] for r in runs], default=""),
            "checks_max": max([c["created_at"] for c in checks], default=""),
            "rfis_max": max([r["created_at"] for r in rfis], default=""),
        }
    except Exception:
        runs, checks, rfis, sig = [], [], [], {"runs_n":0,"checks_n":0,"rfis_n":0,"runs_max":"","checks_max":"","rfis_max":""}

    if st.session_state.get("__export_sig__") != sig:
        st.session_state.pop("__export_zip__", None)
        st.session_state.pop("__export_zip_built_at__", None)
        st.session_state["__export_sig__"] = sig

    st.markdown("### Search & filters")
    q = st.text_input("Search (name / ID / subject)", value="").strip().lower()
    cdf1, cdf2 = st.columns([1, 1])
    with cdf1:
        date_from = st.date_input("From date", value=None, key="sr_from")
    with cdf2:
        date_to = st.date_input("To date", value=None, key="sr_to")
    def _in_range(created_at: str) -> bool:
        if not created_at:
            return True
        try:
            d = datetime.fromisoformat(created_at.replace("Z","")).date()
        except Exception:
            return True
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
        return True


    st.markdown("### Export")
    if st.button("Build ZIP of all saved results", width="stretch"):
        try:
            st.session_state["__export_zip__"] = _build_all_saved_results_zip()
            st.session_state["__export_zip_built_at__"] = _utc_now_iso()
            st.toast("ZIP is ready — download below.")
        except Exception as e:
            st.error(f"Failed to build ZIP: {e}")

    if st.session_state.get("__export_zip__"):
        ts = st.session_state.get("__export_zip_built_at__", "")
        st.download_button(
            label=f"Download all saved results (ZIP){' — ' + ts if ts else ''}",
            data=st.session_state["__export_zip__"],
            file_name=f"fieldflow_saved_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            mime="application/zip",
            width="stretch",
        )

    tab1, tab2, tab3, tab4 = st.tabs(["Schedule runs", "Submittal checks", "RFIs", "Cost estimates"])

    with tab1:
        # Tag / kind filters
        run_meta = {r["id"]: _safe_json(r.get("meta_json")) for r in runs_f}
        all_tags = sorted({tag for mid, m in run_meta.items() for tag in _split_tags(m.get("tags"))})
        all_kinds = sorted({str(run_meta[r["id"]].get("kind","schedule_run")).strip() or "schedule_run" for r in runs_f})

        ctf1, ctf2 = st.columns([1, 1])
        with ctf1:
            tag_pick = st.multiselect("Filter by tags", options=all_tags, default=[], key="sr_tag_pick")
        with ctf2:
            kind_pick = st.multiselect("Filter by kind", options=all_kinds, default=[], key="sr_kind_pick")

        def _run_pass(r):
            if q and not (q in str(r.get("name","")).lower() or q in str(r.get("id","")).lower()):
                return False
            if not _in_range(str(r.get("created_at",""))):
                return False
            m = run_meta.get(r["id"], {})
            tags = set(_split_tags(m.get("tags")))
            kind = str(m.get("kind","schedule_run")).strip() or "schedule_run"
            if tag_pick and not tags.intersection(set(tag_pick)):
                return False
            if kind_pick and kind not in set(kind_pick):
                return False
            return True

        runs_f = [r for r in runs if _run_pass(r)]

        if not runs_f:
            st.caption("No matching schedule runs.")
        else:
            with st.expander("Compare two runs (A/B)", expanded=False):
                names = [f"{r['name']}  ({r['created_at']})" for r in runs_f]
                if len(runs_f) < 2:
                    st.caption("Need at least 2 runs to compare.")
                else:
                    a_idx = st.selectbox("Run A", options=list(range(len(runs_f))), format_func=lambda i: names[i], key="cmp_a")
                    b_idx = st.selectbox("Run B", options=list(range(len(runs_f))), format_func=lambda i: names[i], key="cmp_b")
                    if st.button("Compare", width="stretch"):
                        ra, rb = runs_f[a_idx], runs_f[b_idx]
                        a_df = pd.read_csv(io.StringIO(ra["baseline_csv"]))
                        b_df = pd.read_csv(io.StringIO(rb["baseline_csv"]))
                        # normalize columns if possible
                        a_df = _normalize_cols(a_df) if "Task" in a_df.columns else a_df
                        b_df = _normalize_cols(b_df) if "Task" in b_df.columns else b_df

                        try:
                            a_s = _compute_schedule(a_df)
                            b_s = _compute_schedule(b_df)
                            a_dur = int(a_s["EF"].max() if len(a_s) else 0)
                            b_dur = int(b_s["EF"].max() if len(b_s) else 0)
                            st.metric("Project duration (A)", a_dur)
                            st.metric("Project duration (B)", b_dur)
                            st.metric("Delta (B - A)", b_dur - a_dur)

                            # Float delta table
                            join = a_s[["TaskID","Float"]].merge(b_s[["TaskID","Float"]], on="TaskID", how="outer", suffixes=("_A","_B"))
                            join["Float_A"] = join["Float_A"].fillna(np.nan)
                            join["Float_B"] = join["Float_B"].fillna(np.nan)
                            join["Float_delta"] = join["Float_B"] - join["Float_A"]
                            st.markdown("**Top float erosion (most negative delta)**")
                            st.dataframe(join.sort_values("Float_delta").head(15), width="stretch")
                        except Exception as e:
                            st.error(f"Compare failed: {e}")

            st.markdown("---")
            for r in runs_f:
                st.markdown(f"**{r['name']}**")
                st.caption(f"Saved: {r['created_at']}  |  ID: {r['id']}")
                meta_str = r.get("meta_json") or "{}"
                try:
                    meta_obj = json.loads(meta_str)
                except Exception:
                    meta_obj = {}
                meta_out = {"id": r.get('id'), "created_at": r.get('created_at'), "name": r.get('name'), "meta": meta_obj}
                meta_bytes = json.dumps(meta_out, indent=2).encode('utf-8')

                buf = io.BytesIO()
                with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as z:
                    z.writestr('baseline.csv', (r.get('baseline_csv') or ''))
                    if r.get('crashed_csv'):
                        z.writestr('crashed.csv', r.get('crashed_csv') or '')
                    z.writestr('meta.json', meta_bytes.decode('utf-8'))
                bundle_bytes = buf.getvalue()

                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.download_button(
                        'Download baseline',
                        data=(r.get('baseline_csv') or '').encode('utf-8'),
                        file_name=f"{r['name']}_baseline.csv".replace(' ', '_'),
                        mime='text/csv',
                        key=f"sr_dlb_{r['id']}",
                        width="stretch",
                    )
                with c2:
                    if r.get('crashed_csv'):
                        st.download_button(
                            'Download crashed',
                            data=r.get('crashed_csv').encode('utf-8'),
                            file_name=f"{r['name']}_crashed.csv".replace(' ', '_'),
                            mime='text/csv',
                            key=f"sr_dlc_{r['id']}",
                            width="stretch",
                        )
                    else:
                        st.button('No crashed', disabled=True, key=f"sr_noc_{r['id']}", width="stretch")
                with c3:
                    st.download_button(
                        'Download meta',
                        data=meta_bytes,
                        file_name=f"{r['name']}_meta.json".replace(' ', '_'),
                        mime='application/json',
                        key=f"sr_meta_{r['id']}",
                        width="stretch",
                    )
                with c4:
                    if st.button('Delete', key=f"sr_del_{r['id']}", width="stretch"):
                        delete_schedule_run(r['id'])
                        st.rerun()

                st.download_button(
                    'Download run bundle (ZIP)',
                    data=bundle_bytes,
                    file_name=f"{r['name']}_bundle.zip".replace(' ', '_'),
                    mime='application/zip',
                    key=f"sr_zip_{r['id']}",
                    width="stretch",
                )
                st.markdown('---')

    with tab2:
        checks_f = [c for c in checks if _in_range(str(c.get("created_at","")))]
        if q:
            checks_f = [c for c in checks_f if q in str(c.get("name","")).lower() or q in str(c.get("id","")).lower()]
        if not checks_f:
            st.caption("No matching submittal checks.")
        for c in checks_f:
            st.markdown(f"**{c['name']}**")
            st.caption(f"Saved: {c['created_at']}  |  ID: {c['id']}")
            result_json = c.get('result_json') or '{}'
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    'Download JSON',
                    data=result_json.encode('utf-8'),
                    file_name=f"{c['name']}_result.json".replace(' ', '_'),
                    mime='application/json',
                    key=f"sc_json_{c['id']}",
                    width="stretch",
                )
            with c2:
                if st.button('Delete', key=f"sc_del_{c['id']}", width="stretch"):
                    delete_submittal_check(c['id'])
                    st.rerun()
            st.markdown('---')

    with tab3:
        rfis_f = [r for r in rfis if _in_range(str(r.get("created_at","")))]
        if q:
            rfis_f = [r for r in rfis_f if q in str(r.get("subject","")).lower() or q in str(r.get("id","")).lower()]
        if not rfis_f:
            st.caption("No matching RFIs.")
        for r in rfis_f:
            st.markdown(f"**{r.get('project','')} — {r.get('subject','')}**")
            st.caption(f"Created: {r.get('created_at','')}  |  Status: {r.get('status','')}  |  Due: {r.get('due_date','')}  |  ID: {r.get('id','')}")
            payload = dict(r)
            data = json.dumps(payload, indent=2).encode('utf-8')
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    'Download JSON',
                    data=data,
                    file_name=f"rfi_{r.get('id','')}.json",
                    mime='application/json',
                    key=f"rfi_json_{r.get('id','')}",
                    width="stretch",
                )
            with c2:
                if st.button('Delete', key=f"rfi_del_{r.get('id','')}", width="stretch"):
                    delete_rfi(r.get('id',''))
                    st.rerun()
            st.markdown('---')
    with tab4:
        estimates = list_cost_estimates()
        est_f = [e for e in estimates if _in_range(str(e.get("created_at","")))]
        if q:
            est_f = [e for e in est_f if q in str(e.get("name","")).lower() or q in str(e.get("id","")).lower()]
        if not est_f:
            st.caption("No matching cost estimates.")
        else:
            for e in est_f:
                st.markdown(f"**{e['name']}**")
                st.caption(f"Saved: {e['created_at']}  |  ID: {e['id']}")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.download_button("Download CSV", data=(e.get("estimate_csv") or "").encode("utf-8"), file_name=f"cost_estimate_{e['id']}.csv", mime="text/csv", width="stretch", key=f"estcsv_{e['id']}")
                with c2:
                    st.download_button("Download JSON", data=(e.get("estimate_json") or "{}").encode("utf-8"), file_name=f"cost_estimate_{e['id']}.json", mime="application/json", width="stretch", key=f"estjson_{e['id']}")
                with c3:
                    if e.get("cpm_csv"):
                        st.download_button("Download CPM CSV", data=(e.get("cpm_csv") or "").encode("utf-8"), file_name=f"cost_estimate_cpm_{e['id']}.csv", mime="text/csv", width="stretch", key=f"estcpm_{e['id']}")
                    else:
                        st.caption("No CPM snapshot")
                st.markdown("---")
