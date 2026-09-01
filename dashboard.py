import os
import sys
import difflib
import subprocess
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config import Config
from utils.database_manager import DatabaseManager
from utils.overleaf_connector import OverleafConnector
from agents.base_agent import BaseAgent

st.set_page_config(
    page_title="Research Agents",
    page_icon="🔬",
    layout="wide",
)

# ─── DB ──────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    return DatabaseManager()

db = get_db()

def query_db(sql, params=()):
    with db._get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

# ─── DATA HELPERS ────────────────────────────────────────────────────────────

def get_all_projects():
    # DISTINCT on project_name — guard against duplicate rows from legacy migration
    return query_db(
        "SELECT * FROM project_state GROUP BY project_name ORDER BY project_name"
    )

def get_agent_runs(limit=100):
    return query_db("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,))

def get_overleaf_dirs():
    base = Config.OVERLEAF_DIR
    if not os.path.exists(base):
        return []
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))

STANFORD_ICONS = {
    "READY_FOR_UPLOAD":           "🟡",
    "WAITING_FOR_REVIEW":         "🔵",
    "REVIEW_COMPLETED":           "🟢",
    "INTERNAL_REVIEW_COMPLETED":  "🟢",
    "SKIPPED_INSUFFICIENT_TEXT":  "⚪",
}

RUN_ICONS = {"SUCCESS": "✅", "STARTED": "🔄", "FAILED": "❌", "ERROR": "❌"}

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔬 Research Agents")
    page = st.radio(
        "Navigate",
        ["Overview", "Run Pipeline", "Supervisors", "Literature", "Progress", "Tex Diff", "Agent Logs", "Config", "Analytics"],
        label_visibility="collapsed",
    )
    st.divider()
    if st.button("🔄 Refresh data"):
        st.cache_resource.clear()
        st.rerun()

# ─── OVERVIEW ────────────────────────────────────────────────────────────────

if page == "Overview":
    st.title("Overview")

    projects = get_all_projects()
    dirs = get_overleaf_dirs()
    runs = get_agent_runs(50)
    recent_failures = sum(1 for r in runs if r["status"] in ("FAILED", "ERROR"))

    c1, c2, c3 = st.columns(3)
    c1.metric("Projects in DB", len(projects))
    c2.metric("Local Overleaf dirs", len(dirs))
    c3.metric("Failures (last 50 runs)", recent_failures, delta_color="inverse")

    # ── Charts row ───────────────────────────────────────────────────────────
    _chart_col1, _chart_col2 = st.columns(2)

    with _chart_col1:
        if projects:
            _status_counts = {}
            for _p in projects:
                _s = _p.get("stanford_status") or "N/A"
                _status_counts[_s] = _status_counts.get(_s, 0) + 1
            _status_labels = [f"{STANFORD_ICONS.get(k, '⚪')} {k}" for k in _status_counts]
            _status_colors = []
            for _k in _status_counts:
                if _k in ("REVIEW_COMPLETED", "INTERNAL_REVIEW_COMPLETED"):
                    _status_colors.append("#27ae60")
                elif _k == "WAITING_FOR_REVIEW":
                    _status_colors.append("#2980b9")
                elif _k == "READY_FOR_UPLOAD":
                    _status_colors.append("#f39c12")
                else:
                    _status_colors.append("#95a5a6")
            _fig_pie = go.Figure(go.Pie(
                labels=_status_labels,
                values=list(_status_counts.values()),
                hole=0.4,
                marker_colors=_status_colors,
            ))
            _fig_pie.update_layout(title="Stanford Status Distribution", showlegend=True, margin=dict(t=40, b=0))
            st.plotly_chart(_fig_pie, use_container_width=True)

    with _chart_col2:
        if runs:
            _run_status_counts = {}
            for _r in runs:
                _rs = _r.get("status", "UNKNOWN")
                _run_status_counts[_rs] = _run_status_counts.get(_rs, 0) + 1
            _run_colors = {"SUCCESS": "#27ae60", "FAILED": "#e74c3c", "ERROR": "#e74c3c", "STARTED": "#f39c12"}
            _fig_bar = go.Figure(go.Bar(
                x=list(_run_status_counts.keys()),
                y=list(_run_status_counts.values()),
                marker_color=[_run_colors.get(_k, "#95a5a6") for _k in _run_status_counts],
            ))
            _fig_bar.update_layout(title="Agent Runs by Status (last 50)", xaxis_title="Status", yaxis_title="Count", showlegend=False)
            st.plotly_chart(_fig_bar, use_container_width=True)

    st.divider()
    st.subheader("Projects")

    if not projects:
        st.info("No projects in database. Run the ingestion agent first.")
    else:
        df = pd.DataFrame(projects)
        show_cols = [c for c in ["project_name", "stanford_status", "researcher_email",
                                  "student_name", "supervisor_email"] if c in df.columns]
        df_disp = df[show_cols].copy()
        df_disp["stanford_status"] = df_disp["stanford_status"].apply(
            lambda s: f"{STANFORD_ICONS.get(s, '⚪')} {s}" if s else "⚪ N/A"
        )
        # Replace Python None / NaN with "—" for clean display
        df_disp = df_disp.fillna("—").replace("None", "—")
        st.dataframe(df_disp, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Local Overleaf Projects")
    if dirs:
        db_names = {p["project_name"] for p in projects}
        for d in dirs:
            in_db = d in db_names
            badge = "✅ synced" if in_db else "⚠️ not in DB"
            proj_path = os.path.join(Config.OVERLEAF_DIR, d)
            try:
                files = sorted(os.listdir(proj_path))[:20]
            except OSError:
                files = []
            with st.expander(f"**{d}** — {badge}"):
                st.caption("Files: " + (", ".join(files) if files else "empty"))
    else:
        st.info("No local Overleaf project directories found.")

# ─── RUN PIPELINE ────────────────────────────────────────────────────────────

elif page == "Run Pipeline":
    st.title("Run Pipeline")

    dirs = get_overleaf_dirs()

    col1, col2 = st.columns(2)
    with col1:
        agent = st.selectbox(
            "Agent",
            ["all", "ingestion", "literature", "progress", "enhancement", "supervisor", "gc"],
        )
    with col2:
        project = st.selectbox("Project", ["all"] + dirs)

    st.caption(
        "**Agents:** `ingestion` — sync Overleaf · `literature` — fetch papers · "
        "`progress` — track writing · `enhancement` — peer review · `gc` — cleanup"
    )

    if st.button("▶ Run", type="primary", use_container_width=True):
        cmd = [sys.executable, str(ROOT / "main.py"), "--agent", agent, "--project", project]
        st.info(f"`{' '.join(cmd)}`")

        with st.status("Running…", expanded=True) as status_box:
            output_area = st.empty()
            lines: list[str] = []
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(ROOT),
                )
                for line in proc.stdout:
                    lines.append(line.rstrip())
                    output_area.code("\n".join(lines[-60:]))
                proc.wait()
                if proc.returncode == 0:
                    status_box.update(label="✅ Finished successfully", state="complete")
                else:
                    status_box.update(label=f"❌ Exited with code {proc.returncode}", state="error")
            except Exception as exc:
                status_box.update(label=f"❌ Launch error: {exc}", state="error")

        st.rerun()

# ─── SUPERVISORS ─────────────────────────────────────────────────────────────

elif page == "Supervisors":
    st.title("Supervisor Assignments")
    st.caption("Assign a supervisor and student name to each project. The Supervisor agent uses these to send weekly lab reports.")

    projects = get_all_projects()
    if not projects:
        st.info("No projects in database. Run the ingestion agent first.")
        st.stop()

    # ── Bulk table view ───────────────────────────────────────────────────────
    st.subheader("Current Assignments")
    show_cols = ["project_name", "researcher_email", "student_name", "supervisor_email"]
    df = pd.DataFrame(projects)
    existing_cols = [c for c in show_cols if c in df.columns]
    st.dataframe(
        df[existing_cols].fillna("—").replace("None", "—"),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── Edit form ─────────────────────────────────────────────────────────────
    st.subheader("Edit Assignment")

    project_names = [p["project_name"] for p in projects]
    selected = st.selectbox("Project", project_names)

    current = next((p for p in projects if p["project_name"] == selected), {})

    with st.form("supervisor_form"):
        col1, col2 = st.columns(2)
        with col1:
            researcher_email = st.text_input(
                "Researcher email (student receives agent emails)",
                value=current.get("researcher_email") or "",
            )
            student_name = st.text_input(
                "Student name",
                value=current.get("student_name") or "",
            )
        with col2:
            supervisor_email = st.text_input(
                "Supervisor email (receives weekly lab report)",
                value=current.get("supervisor_email") or "",
            )
            update_frequency = st.selectbox(
                "Update frequency",
                ["daily", "weekly", "bi-weekly", "monthly"],
                index=["daily", "weekly", "bi-weekly", "monthly"].index(
                    current.get("update_frequency") or "weekly"
                ) if (current.get("update_frequency") or "weekly") in ["daily", "weekly", "bi-weekly", "monthly"] else 1,
            )

        submitted = st.form_submit_button("💾 Save", type="primary", use_container_width=True)

    if submitted:
        updates = {}
        if researcher_email.strip():
            updates["researcher_email"] = researcher_email.strip()
        if student_name.strip():
            updates["student_name"] = student_name.strip()
        if supervisor_email.strip():
            updates["supervisor_email"] = supervisor_email.strip()
        updates["update_frequency"] = update_frequency

        if updates:
            db.update_project_state(selected, **updates)
            st.success(f"Saved assignments for **{selected}**.")
            st.rerun()
        else:
            st.warning("No values provided — nothing saved.")

    st.divider()

    # ── Quick-run supervisor agent ────────────────────────────────────────────
    st.subheader("Run Supervisor Agent")
    st.caption("Sends weekly lab report emails to all supervisors with assigned projects.")
    if st.button("▶ Run Supervisor Agent now", type="secondary"):
        cmd = [sys.executable, str(ROOT / "main.py"), "--agent", "supervisor"]
        with st.status("Running…", expanded=True) as status_box:
            output_area = st.empty()
            lines: list[str] = []
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=str(ROOT),
                )
                for line in proc.stdout:
                    lines.append(line.rstrip())
                    output_area.code("\n".join(lines[-40:]))
                proc.wait()
                if proc.returncode == 0:
                    status_box.update(label="✅ Done", state="complete")
                else:
                    status_box.update(label=f"❌ Exit code {proc.returncode}", state="error")
            except Exception as exc:
                status_box.update(label=f"❌ {exc}", state="error")

# ─── LITERATURE ──────────────────────────────────────────────────────────────

elif page == "Literature":
    st.title("Literature")

    dirs = get_overleaf_dirs()
    if not dirs:
        st.info("No projects found.")
        st.stop()

    project = st.selectbox("Project", dirs)
    safe = project.replace(" ", "_")
    csv_path = os.path.join(Config.LIBRARY_DIR, "comparison_tables", safe, f"{safe}_rolling_table.csv")

    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        st.write(f"**{len(df)} papers**")
        st.dataframe(df, use_container_width=True)

        if "year published" in df.columns:
            year_vals = df["year published"].dropna().astype(str)
            year_vals = year_vals[year_vals.str.match(r"^\d{4}$")]
            if not year_vals.empty:
                counts = year_vals.value_counts().sort_index().reset_index()
                counts.columns = ["Year", "Count"]
                fig = px.bar(counts, x="Year", y="Count", title="Papers by Year")
                st.plotly_chart(fig, use_container_width=True)
        # Citation count histogram
        if "citationCount" in df.columns:
            _cite_vals = pd.to_numeric(df["citationCount"], errors="coerce").dropna()
            if not _cite_vals.empty:
                _bins = [0, 10, 50, 200, float("inf")]
                _labels = ["0–10", "11–50", "51–200", "200+"]
                _cite_binned = pd.cut(_cite_vals, bins=_bins, labels=_labels, right=True)
                _cite_counts = _cite_binned.value_counts().reindex(_labels).fillna(0).reset_index()
                _cite_counts.columns = ["Citations", "Count"]
                _fig_cite = px.bar(_cite_counts, x="Citations", y="Count", title="Papers by Citation Count")
                st.plotly_chart(_fig_cite, use_container_width=True)

        # Reproducibility / complexity pie charts
        _pie_cols = [c for c in ["reproducibility", "complexity"] if c in df.columns]
        if _pie_cols:
            _pie_chart_cols = st.columns(len(_pie_cols))
            for _ci, _col in enumerate(_pie_cols):
                _vc = df[_col].dropna().value_counts().reset_index()
                _vc.columns = [_col, "Count"]
                _fig_pc = px.pie(_vc, names=_col, values="Count", title=_col.capitalize())
                _pie_chart_cols[_ci].plotly_chart(_fig_pc, use_container_width=True)

    else:
        st.info(f"No literature table for **{project}** yet. Run the literature agent first.")

    # Markdown summaries
    review_dir = os.path.join(Config.LIBRARY_DIR, "literature_reviews", safe)
    if os.path.exists(review_dir):
        md_files = sorted(
            [f for f in os.listdir(review_dir) if f.endswith(".md")], reverse=True
        )
        if md_files:
            st.divider()
            st.subheader("Summaries")
            chosen = st.selectbox("File", md_files)
            try:
                with open(os.path.join(review_dir, chosen), encoding="utf-8") as f:
                    st.markdown(f.read())
            except OSError as e:
                st.error(f"Could not read file: {e}")

# ─── PROGRESS ────────────────────────────────────────────────────────────────

elif page == "Progress":
    st.title("Writing Progress")

    dirs = get_overleaf_dirs()
    if not dirs:
        st.info("No projects found.")
        st.stop()

    project = st.selectbox("Project", dirs)
    days = st.slider("Days to show", 7, 90, 28)
    snapshots = db.get_project_snapshots(project, days=days)

    if not snapshots:
        st.info("No progress snapshots yet. Run the progress agent first.")
    else:
        df = pd.DataFrame(snapshots)
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
        df = df.sort_values("snapshot_date")
        # SQLite stores BOOLEAN as 0/1 integers; cast to bool so pandas filters rows, not columns
        df["had_changes"] = df["had_changes"].astype(bool)

        colors = ["#27ae60" if c else "#c0392b" for c in df["had_changes"]]
        fig = go.Figure(go.Bar(
            x=df["snapshot_date"].astype(str),
            y=df["delta_char_count"],
            marker_color=colors,
            hovertemplate="%{x}<br>%{y} chars<extra></extra>",
        ))
        fig.update_layout(
            title=f"Daily Writing Activity — last {days} days",
            xaxis_title="Date",
            yaxis_title="Characters added",
            showlegend=False,
            xaxis=dict(type="category"),  # prevent Plotly treating strings as datetime axis
        )
        st.plotly_chart(fig, use_container_width=True)

        active = int(df["had_changes"].sum())
        silent = int((~df["had_changes"]).sum())
        active_chars = df[df["had_changes"]]["delta_char_count"]
        avg_chars = f"{active_chars.mean():.0f}" if active else "—"

        # Cumulative chars line chart
        _df_cum = df.sort_values("snapshot_date").copy()
        _df_cum["cumulative_chars"] = _df_cum["delta_char_count"].cumsum()
        _fig_cum = go.Figure(go.Scatter(
            x=_df_cum["snapshot_date"].astype(str),
            y=_df_cum["cumulative_chars"],
            fill="tozeroy",
            mode="lines",
            line=dict(color="#2980b9"),
            hovertemplate="%{x}<br>%{y:,} chars total<extra></extra>",
        ))
        _fig_cum.update_layout(
            title="Cumulative Writing Progress",
            xaxis_title="Date",
            yaxis_title="Cumulative characters",
            xaxis=dict(type="category"),
        )
        st.plotly_chart(_fig_cum, use_container_width=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active days", active)
        c2.metric("Silent days", silent)
        c3.metric("Avg chars / active day", avg_chars)
        _pct = f"{100*active/(active+silent):.0f}%" if (active + silent) > 0 else "—"
        c4.metric("% active days", _pct)

    # AI feedback files
    safe = project.replace(" ", "_")
    tracking_dir = os.path.join(Config.LIBRARY_DIR, "project_tracking", safe)
    if os.path.exists(tracking_dir):
        md_files = sorted(
            [f for f in os.listdir(tracking_dir) if f.endswith(".md")], reverse=True
        )
        if md_files:
            st.divider()
            st.subheader("AI Feedback")
            chosen = st.selectbox("File", md_files)
            try:
                with open(os.path.join(tracking_dir, chosen), encoding="utf-8") as f:
                    st.markdown(f.read())
            except OSError as e:
                st.error(f"Could not read file: {e}")

    # Stanford / internal review
    enhance_dir = os.path.join(Config.LIBRARY_DIR, "project_enhancement", safe)
    review_file = os.path.join(enhance_dir, "stanford_tasks.md")
    if os.path.exists(review_file):
        st.divider()
        st.subheader("Peer Review & Action Plan")
        try:
            with open(review_file, encoding="utf-8") as f:
                st.markdown(f.read())
        except OSError as e:
            st.error(f"Could not read review: {e}")

# ─── TEX DIFF ────────────────────────────────────────────────────────────────

elif page == "Tex Diff":
    st.title("Tex Changes")

    dirs = get_overleaf_dirs()
    if not dirs:
        st.info("No projects found.")
        st.stop()

    project = st.selectbox("Project", dirs)
    connector = OverleafConnector()
    project_path = os.path.join(Config.OVERLEAF_DIR, project)

    # ── Current text from disk (all .tex files) ───────────────────────────────
    current_text = connector.read_all_tex_files(project_path)

    if not current_text:
        st.warning("No .tex files found for this project.")
        st.stop()

    # ── Last-seen text from DB ────────────────────────────────────────────────
    state = db.get_project_state(project)
    last_text = (state.get("last_seen_text") or "") if state else ""

    # ── Stats row ─────────────────────────────────────────────────────────────
    cur_words = len(current_text.split())
    prev_words = len(last_text.split()) if last_text else 0
    delta_words = cur_words - prev_words

    c1, c2, c3 = st.columns(3)
    c1.metric("Current word count", f"{cur_words:,}")
    c2.metric("Previous word count", f"{prev_words:,}" if last_text else "—")
    c3.metric("Word delta", f"{delta_words:+,}" if last_text else "—",
              delta_color="normal" if delta_words >= 0 else "inverse")

    st.divider()

    if not last_text:
        st.info("No previous version in DB yet. Run the progress agent first to establish a baseline.")
        st.subheader("Current Text")
        st.code(current_text[:6000] + ("…" if len(current_text) > 6000 else ""), language="latex")
        st.stop()

    # ── Diff ──────────────────────────────────────────────────────────────────
    old_lines = last_text.splitlines()
    new_lines = current_text.splitlines()

    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="Previous (DB)",
        tofile="Current (disk)",
        lineterm="",
        n=3,
    ))

    tab_diff, tab_delta, tab_side = st.tabs(["Unified Diff", "Additions Only", "Side by Side"])

    with tab_diff:
        if not diff_lines:
            st.success("No changes — current text matches the last processed version.")
        else:
            st.caption(f"{sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))} lines added · "
                       f"{sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))} lines removed")
            # Color-coded diff via HTML
            html_lines = []
            for line in diff_lines:
                if line.startswith("+++") or line.startswith("---"):
                    html_lines.append(f'<div style="color:#888;font-family:monospace;font-size:12px">{line}</div>')
                elif line.startswith("@@"):
                    html_lines.append(f'<div style="color:#5b9bd5;font-family:monospace;font-size:12px">{line}</div>')
                elif line.startswith("+"):
                    esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    html_lines.append(f'<div style="background:#1a3a1a;color:#5cb85c;font-family:monospace;font-size:12px;padding:1px 4px">{esc}</div>')
                elif line.startswith("-"):
                    esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    html_lines.append(f'<div style="background:#3a1a1a;color:#d9534f;font-family:monospace;font-size:12px;padding:1px 4px">{esc}</div>')
                else:
                    esc = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    html_lines.append(f'<div style="font-family:monospace;font-size:12px;padding:1px 4px;color:#ccc">{esc}</div>')
            st.html(
                f'<div style="background:#1e1e1e;padding:8px;border-radius:6px;max-height:600px;overflow-y:auto">'
                + "".join(html_lines) + "</div>"
            )

    with tab_delta:
        added = [l[1:] for l in diff_lines if l.startswith("+") and not l.startswith("+++")]
        if not added:
            st.success("No new lines added.")
        else:
            st.caption(f"{len(added)} lines of new content")
            st.code("\n".join(added), language="latex")

    with tab_side:
        MAX_LINES = 200
        old_capped = old_lines[:MAX_LINES]
        new_capped = new_lines[:MAX_LINES]
        if len(old_lines) > MAX_LINES or len(new_lines) > MAX_LINES:
            st.caption(f"Showing first {MAX_LINES} lines of each side.")
        hd = difflib.HtmlDiff(wrapcolumn=80)
        side_html = hd.make_table(
            old_capped, new_capped,
            fromdesc="Previous (DB)", todesc="Current (disk)",
            context=True, numlines=3,
        )
        styled = (
            '<style>'
            'table.diff {width:100%;font-family:monospace;font-size:12px;border-collapse:collapse}'
            'table.diff td,table.diff th {padding:2px 6px;vertical-align:top;white-space:pre-wrap;word-break:break-all}'
            'table.diff .diff_header {background:#2a2a2a;color:#888;text-align:right}'
            '.diff_next {display:none}'
            'table.diff .diff_add {background:#1a3a1a;color:#5cb85c}'
            'table.diff .diff_chg {background:#3a3a1a;color:#e6c84b}'
            'table.diff .diff_sub {background:#3a1a1a;color:#d9534f}'
            '</style>'
            + side_html
        )
        st.html(f'<div style="overflow-x:auto;background:#1e1e1e;padding:8px;border-radius:6px">{styled}</div>')

    # ── Latest AI change summary ──────────────────────────────────────────────
    safe = project.replace(" ", "_")
    tracking_dir = os.path.join(Config.LIBRARY_DIR, "project_tracking", safe)
    if os.path.exists(tracking_dir):
        md_files = sorted([f for f in os.listdir(tracking_dir) if f.endswith(".md")], reverse=True)
        if md_files:
            st.divider()
            st.subheader("Latest AI Summary of Changes")
            try:
                with open(os.path.join(tracking_dir, md_files[0]), encoding="utf-8") as f:
                    st.markdown(f.read())
            except OSError as e:
                st.error(str(e))

# ─── AGENT LOGS ──────────────────────────────────────────────────────────────

elif page == "Agent Logs":
    st.title("Agent Logs")

    tab_db, tab_file = st.tabs(["Run History (DB)", "Log Files"])

    with tab_db:
        limit = st.slider("Show last N runs", 20, 500, 100)
        raw = get_agent_runs(limit)
        if not raw:
            st.info("No runs recorded yet.")
        else:
            df = pd.DataFrame(raw)

            # Compute duration_s before display
            def _parse_dur(row):
                try:
                    s = datetime.fromisoformat(row["started_at"])
                    f = datetime.fromisoformat(row["finished_at"])
                    return round((f - s).total_seconds(), 1)
                except Exception:
                    return None
            if "started_at" in df.columns and "finished_at" in df.columns:
                df["duration_s"] = df.apply(_parse_dur, axis=1)
                df["duration_s"] = df["duration_s"].apply(lambda v: "—" if v is None else v)

            df["status"] = df["status"].apply(lambda s: f"{RUN_ICONS.get(s, '⚪')} {s}")
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Success rate per agent bar chart
            _agent_stats = {}
            for _r in raw:
                _an = _r.get("agent_name", "unknown")
                if _an not in _agent_stats:
                    _agent_stats[_an] = {"total": 0, "success": 0}
                _agent_stats[_an]["total"] += 1
                if _r.get("status") == "SUCCESS":
                    _agent_stats[_an]["success"] += 1
            if _agent_stats:
                _agents = list(_agent_stats.keys())
                _successes = [_agent_stats[a]["success"] for a in _agents]
                _failures = [_agent_stats[a]["total"] - _agent_stats[a]["success"] for a in _agents]
                _fig_agent = go.Figure(data=[
                    go.Bar(name="SUCCESS", x=_agents, y=_successes, marker_color="#27ae60"),
                    go.Bar(name="FAILED", x=_agents, y=_failures, marker_color="#e74c3c"),
                ])
                _fig_agent.update_layout(barmode="stack", title="Runs per Agent (Success vs Failed)", xaxis_title="Agent", yaxis_title="Count")
                st.plotly_chart(_fig_agent, use_container_width=True)

            # Error messages only
            errors = [r for r in raw if r.get("error_message")]
            if errors:
                with st.expander(f"⚠️ {len(errors)} runs with error messages"):
                    for r in errors:
                        st.error(f"**{r['agent_name']} / {r['project_name']}** — {r['error_message']}")

    with tab_file:
        log_dir = Config.LOGS_DIR
        if os.path.exists(log_dir):
            log_files = sorted(
                [f for f in os.listdir(log_dir) if f.endswith(".log")], reverse=True
            )
            if log_files:
                chosen = st.selectbox("Log file", log_files)
                n_lines = st.number_input("Last N lines", min_value=10, max_value=500, value=60, step=10)
                log_path = os.path.join(log_dir, chosen)
                try:
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        all_lines = f.readlines()
                    st.code("".join(all_lines[-int(n_lines):]), language="")
                except OSError as e:
                    st.error(f"Could not read log: {e}")
            else:
                st.info("No .log files found.")
        else:
            st.info("Log directory does not exist yet.")

# ─── CONFIG ──────────────────────────────────────────────────────────────────

elif page == "Config":
    st.title("Configuration")

    def mask(val):
        if not val:
            return "❌ NOT SET"
        if len(str(val)) <= 8:
            return "✅ SET (hidden)"
        s = str(val)
        return f"✅ {s[:4]}…{s[-4:]}"

    st.subheader("API Keys")
    st.table(pd.DataFrame({
        "Key": ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"],
        "Status": [
            mask(Config.GROQ_API_KEY),
            mask(getattr(Config, "GEMINI_API_KEY", None)),
            mask(getattr(Config, "OPENAI_API_KEY", None)),
        ],
    }))

    st.subheader("Email")
    st.table(pd.DataFrame({
        "Key": ["OVERLEAF_EMAIL (login)", "RESEARCHER_EMAIL (notifications)", "NOTIFICATION_SENDER_EMAIL"],
        "Value": [
            Config.OVERLEAF_EMAIL or "❌ NOT SET",
            Config.RESEARCHER_EMAIL or "❌ NOT SET",
            Config.NOTIFICATION_SENDER_EMAIL or "❌ NOT SET",
        ],
    }))

    st.subheader("Paths")
    path_rows = []
    for key, path in [
        ("OVERLEAF_DIR", Config.OVERLEAF_DIR),
        ("LIBRARY_DIR", Config.LIBRARY_DIR),
        ("LOGS_DIR", Config.LOGS_DIR),
    ]:
        path_rows.append({
            "Key": key,
            "Path": str(path),
            "Exists": "✅" if os.path.exists(path) else "❌",
        })
    st.table(pd.DataFrame(path_rows))

    st.subheader("LLM & System Settings")
    st.table(pd.DataFrame({
        "Setting": ["Primary model", "Max LLM retries", "LLM timeout (s)",
                    "Email max retries", "GC retention (days)"],
        "Value": [
            Config.LLM_MODEL_NAME,
            Config.LLM_MAX_RETRIES,
            Config.LLM_TIMEOUT_SECONDS,
            Config.EMAIL_MAX_RETRIES,
            Config.GARBAGE_COLLECTION_TTL_DAYS,
        ],
    }))

    st.subheader("LLM Waterfall Status")
    # Order comes from BaseAgent.PROVIDER_ORDER — the single source of truth also
    # used to build the real waterfall in BaseAgent._setup_llm() — instead of a
    # second, independently-maintained literal here, so this table structurally
    # cannot drift out of sync with the real waterfall the way it previously did
    # (it used to omit Gemma 4 and NVIDIA NIM entirely and mislabel OpenAI's
    # fallback position). Gemma 4 has no separate API key — it's gated purely on
    # the Gemini key/client (see BaseAgent._setup_llm) — so its "Configured" status
    # mirrors Gemini's here.
    _PROVIDER_DISPLAY = {
        "groq": ("Groq", getattr(Config, "GROQ_API_KEY", None), getattr(Config, "LLM_MODEL_NAME", "openai/gpt-oss-120b")),
        "gemini": ("Gemini", getattr(Config, "GEMINI_API_KEY", None), getattr(Config, "GEMINI_MODEL_NAME", "gemini-2.5-flash")),
        "gemma": ("Gemma 4", getattr(Config, "GEMINI_API_KEY", None), getattr(Config, "GEMMA_MODEL_NAME", "models/gemma-4-26b-a4b-it")),
        "nvidia_nim": ("NVIDIA NIM", getattr(Config, "NVIDIA_NIM_API_KEY", None), getattr(Config, "NVIDIA_NIM_MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")),
        "openai": ("OpenAI", getattr(Config, "OPENAI_API_KEY", None), getattr(Config, "OPENAI_MODEL_NAME", "gpt-4o-mini")),
    }
    _roles = ["Primary"] + [f"Fallback {i}" for i in range(1, len(BaseAgent.PROVIDER_ORDER))]
    st.table(pd.DataFrame({
        "Provider": [_PROVIDER_DISPLAY[p][0] for p in BaseAgent.PROVIDER_ORDER],
        "Role": _roles,
        "Model": [_PROVIDER_DISPLAY[p][2] for p in BaseAgent.PROVIDER_ORDER],
        "Configured": ["✅" if _PROVIDER_DISPLAY[p][1] else "❌" for p in BaseAgent.PROVIDER_ORDER],
    }))

# ─── ANALYTICS ───────────────────────────────────────────────────────────────

elif page == "Analytics":
    st.title("Analytics")

    st.subheader("Writing Activity — last 28 days (all projects)")
    _activity_rows = query_db(
        "SELECT project_name, SUM(delta_char_count) as total_chars, "
        "SUM(CASE WHEN had_changes=1 THEN 1 ELSE 0 END) as active_days "
        "FROM progress_snapshots WHERE snapshot_date >= date('now', '-28 days') "
        "GROUP BY project_name ORDER BY total_chars DESC"
    )
    if _activity_rows:
        _df_act = pd.DataFrame(_activity_rows)
        _fig_act = px.bar(
            _df_act,
            x="total_chars",
            y="project_name",
            orientation="h",
            title="Total characters written per project (last 28 days)",
            labels={"total_chars": "Characters written", "project_name": "Project"},
        )
        _fig_act.update_layout(yaxis=dict(autorange="reversed"))
        st.plotly_chart(_fig_act, use_container_width=True)
    else:
        st.info("No writing activity data for the last 28 days.")

    st.subheader("Agent Success Rate Summary")
    _success_rows = query_db(
        "SELECT agent_name, COUNT(*) as total, "
        "SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) as successes "
        "FROM agent_runs GROUP BY agent_name"
    )
    if _success_rows:
        _df_suc = pd.DataFrame(_success_rows)
        _df_suc["success_rate"] = (_df_suc["successes"] / _df_suc["total"] * 100).round(1)
        st.dataframe(_df_suc, use_container_width=True, hide_index=True)
    else:
        st.info("No agent run data available.")
