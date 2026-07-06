"""Streamlit UI for the Ranking Videos Generator.

Run with: ``streamlit run app.py``

The UI is intentionally minimal: a sidebar for project settings and an editable
clip table with validate / dry run / render buttons and a progress view.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config_schema import AppConfig, DEFAULT_CONFIG
from src.renderer import dry_run, render_project
from src.ranking import load_project_json, validate_clips
from src.ui_helpers import (
    DEFAULT_ROW,
    import_csv_bytes,
    import_json_bytes,
    project_to_rows,
    rows_to_project,
    validation_status,
    render_plan_table,
)


def _config_from_sidebar() -> AppConfig:
    return AppConfig.from_env()


def main() -> None:
    st.set_page_config(page_title="Ranking Videos Generator", layout="wide")
    st.title("Ranking Videos Generator")
    st.caption("Generate vertical 9:16 ranking / top-list Shorts from clip URLs.")

    with st.sidebar:
        st.header("Project settings")
        title = st.text_input("Project title", value="Ranking Best Moments")
        resolution = st.selectbox("Output resolution", ["1080x1920", "720x1280"], index=0)
        transition = st.selectbox("Transitions", ["fade", "slide", "none"], index=0)
        detect_default = st.selectbox(
            "Default detection mode",
            ["auto", "manual", "center"],
            index=0,
            help="auto = heuristic pipeline; manual = use start/end; center = middle of clip",
        )
        dry = st.checkbox("Dry run (validate only)", value=False)
        st.divider()
        st.subheader("Import config")
        upload = st.file_uploader(
            "Upload JSON or CSV", type=["json", "csv"],
        )

    # ---- Editable clip table ----
    st.subheader("Clip list")
    st.caption("Tip: `detection_mode` must be one of `auto`, `manual`, `center`, or `full`. "
               "For `manual`, set both `start_time` and `end_time`. "
               "Check `flip` to mirror the clip horizontally (anti-strike).")

    if "rows" not in st.session_state:
        st.session_state.rows = [dict(DEFAULT_ROW)]

    # Import from uploaded file — only when the file name changes (otherwise
    # Streamlit re-runs would re-import and reset user edits).
    if upload is not None:
        upload_id = upload.name + str(upload.size)
        if st.session_state.get("_uploaded_id") != upload_id:
            try:
                if upload.name.lower().endswith(".json"):
                    proj = import_json_bytes(upload.getvalue())
                else:
                    proj = import_csv_bytes(upload.getvalue())
                st.session_state.rows = project_to_rows(proj)
                st.session_state["_uploaded_id"] = upload_id
                if proj.project_title:
                    title = proj.project_title
                st.rerun()
            except Exception as exc:
                st.error(f"Import failed: {exc}")

    # Manual row editor — avoids AG Grid Enter-key race condition where
    # the first cell edit is lost because Enter also navigates to the
    # cell below (adding a new empty row via num_rows="dynamic").
    headers = st.columns([1, 4, 2, 1, 1, 1, 1, 0.2])
    for h, label in zip(headers, ["Rank", "URL", "Caption", "Start", "End", "Mode", "Flip", ""]):
        h.markdown(f"**{label}**" if label else "")

    rows = []
    for i, row in enumerate(st.session_state.rows):
        row_id = row.get("_id") or hash(repr(row)) % (2**31)
        cols = st.columns([1, 4, 2, 1, 1, 1, 1, 0.2])
        with cols[0]:
            rank = st.text_input("Rank", value=str(row.get("rank", i + 1)),
                                 key=f"r_{row_id}_rank", label_visibility="collapsed")
        with cols[1]:
            url = st.text_input("URL", value=row.get("url", ""),
                                key=f"r_{row_id}_url", label_visibility="collapsed",
                                placeholder="https://...")
        with cols[2]:
            caption = st.text_input("Caption", value=row.get("caption", ""),
                                    key=f"r_{row_id}_caption", label_visibility="collapsed")
        with cols[3]:
            start = st.text_input("Start", value=str(row.get("start_time") or ""),
                                  key=f"r_{row_id}_start", label_visibility="collapsed",
                                  placeholder="s")
        with cols[4]:
            end = st.text_input("End", value=str(row.get("end_time") or ""),
                                key=f"r_{row_id}_end", label_visibility="collapsed",
                                placeholder="e")
        with cols[5]:
            mode = st.selectbox("Mode", ["auto", "manual", "center", "full"],
                                index=["auto", "manual", "center", "full"].index(
                                    row.get("detection_mode", "auto")),
                                key=f"r_{row_id}_mode", label_visibility="collapsed")
        with cols[6]:
            flip = st.checkbox("Flip", value=bool(row.get("horizontal_flip", False)),
                               key=f"r_{row_id}_flip", label_visibility="collapsed")
        with cols[7]:
            if st.button("✕", key=f"r_{row_id}_del", help="Remove clip"):
                st.session_state.rows.pop(i)
                st.rerun()
        rows.append({
            "_id": row_id,
            "rank": int(rank) if rank.isdigit() else i + 1,
            "url": url,
            "caption": caption,
            "start_time": float(start) if start else None,
            "end_time": float(end) if end else None,
            "detection_mode": mode,
            "horizontal_flip": flip,
        })
    st.session_state.rows = rows

    if st.button("+ Add row", type="secondary"):
        st.session_state.rows.append(dict(DEFAULT_ROW, _id=hash(str(st.session_state.rows))))
        st.rerun()

    project = rows_to_project(title, rows)
    if not project.clips:
        st.info("Add at least one clip row with a valid URL to begin.")

    # ---- Validation preview ----
    st.subheader("Validation & preview")
    errors, warnings = validation_status(project)
    if errors:
        st.error("Errors:\n- " + "\n- ".join(errors))
    if warnings:
        st.warning("Warnings:\n- " + "\n- ".join(warnings))
    if not errors and project.clips:
        st.success(f"{len(project.clips)} clips look valid.")

    st.dataframe(render_plan_table(project), width="stretch")

    # ---- Actions ----
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Validate", type="secondary", disabled=not project.clips):
            errors, warnings = validation_status(project)
            if errors:
                st.error("Fix errors before rendering.")
            else:
                st.success("Validation passed.")
    with col2:
        if st.button("Dry run", disabled=not project.clips):
            with st.spinner("Running dry run..."):
                plan = dry_run(project, _config_from_sidebar())
            st.text(plan.summary())
    with col3:
        if st.button("Render", type="primary", disabled=not project.clips or dry):
            cfg = _config_from_sidebar()
            progress = st.progress(0.0, text="Starting render...")
            status = st.empty()

            def _cb(stage: str, current: int, total: int) -> None:
                frac = current / total if total else 0.0
                progress.progress(min(frac, 1.0), text=f"{stage} ({current}/{total})")
                status.write(f"Stage: **{stage}** — clip {current}/{total}")

            try:
                out = render_project(project, cfg, progress_cb=_cb)
                progress.progress(1.0, text="Done")
                st.success(f"Render complete: {out}")
                if out.exists():
                    st.video(str(out))
            except Exception as exc:
                st.error(f"Render failed: {exc}")


if __name__ == "__main__":
    main()