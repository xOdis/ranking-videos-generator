"""Streamlit UI helpers — keeps :mod:`app` thin and testable.

All UI-side parsing, table building and CSV/JSON import helpers live here so
they can be unit-tested without spawning Streamlit.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .ranking import parse_clip_dict, validate_clips
from .models import ProjectDefinition


DEFAULT_ROW: dict[str, Any] = {
    "rank": 1,
    "url": "",
    "caption": "",
    "start_time": "",
    "end_time": "",
    "detection_mode": "auto",
    "horizontal_flip": False,
}


def rows_to_project(title: str, rows: list[dict[str, Any]]) -> ProjectDefinition:
    """Convert editable UI rows into a :class:`ProjectDefinition`."""
    parsed = []
    for r in rows:
        cleaned = {k: ("" if v is None else v) for k, v in r.items()}
        # skip empty rows
        if not str(cleaned.get("url", "")).strip():
            continue
        try:
            parsed.append(parse_clip_dict(cleaned))
        except Exception:  # noqa: BLE001
            continue
    return ProjectDefinition(project_title=title, clips=parsed)


def import_csv_bytes(raw: bytes) -> ProjectDefinition:
    """Parse uploaded CSV bytes into a project (title taken from a column if present)."""
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    title = "Imported Project"
    clips = []
    for r in rows:
        if not str(r.get("url", "")).strip():
            continue
        try:
            clips.append(parse_clip_dict(r))
        except Exception:  # noqa: BLE001
            continue
    if rows and "project_title" in (reader.fieldnames or []) and rows[0].get("project_title"):
        title = rows[0]["project_title"]
    return ProjectDefinition(project_title=title, clips=clips)


def import_json_bytes(raw: bytes) -> ProjectDefinition:
    """Parse uploaded JSON bytes into a project."""
    data = json.loads(raw.decode("utf-8-sig"))
    clips = []
    for c in data.get("clips", []):
        try:
            clips.append(parse_clip_dict(c))
        except Exception:  # noqa: BLE001
            continue
    return ProjectDefinition(
        project_title=data.get("project_title", "Imported Project"),
        clips=clips,
        output_resolution=data.get("output_resolution", "1080x1920"),
        transition_style=data.get("transition_style", "fade"),
    )


def project_to_rows(project: ProjectDefinition) -> list[dict[str, Any]]:
    """Flatten a project into editable rows for the Streamlit data editor."""
    rows = []
    for c in project.clips:
        rows.append({
            "rank": c.rank,
            "url": c.url,
            "caption": c.caption,
            "start_time": "" if c.start_time is None else c.start_time,
            "end_time": "" if c.end_time is None else c.end_time,
            "detection_mode": c.detection_mode.value,
            "horizontal_flip": c.horizontal_flip,
        })
    return rows


def validation_status(project: ProjectDefinition) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for display in the UI."""
    issues = validate_clips(project.clips)
    errors = [i for i in issues if "bad URL" in i or "duplicate" in i or "manual mode" in i]
    warnings = [i for i in issues if i not in errors]
    return errors, warnings


def render_plan_table(project: ProjectDefinition) -> list[dict[str, Any]]:
    """Produce a preview table of the parsed ranking (used for the dry-run UI)."""
    rows = []
    for c in project.sorted_by_rank():
        rows.append({
            "rank": c.rank,
            "url": c.url,
            "caption": c.caption,
            "mode": c.detection_mode.value,
            "flip": c.horizontal_flip,
            "start": c.start_time,
            "end": c.end_time,
        })
    return rows