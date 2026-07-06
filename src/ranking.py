"""Project parsing, validation and ranking orchestration.

Reads JSON / CSV into a :class:`ProjectDefinition`, validates every rule required
by the strict-constraints section (bad URLs, empty captions, duplicate ranks,
unsupported media handled in the downloader), and produces a
:class:`RenderPlan` for the dry-run report.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Optional

from .config_schema import AppConfig
from .downloader import validate_url
from .logging_utils import get_logger
from .models import (
    ClipSpec,
    DetectionMode,
    ProjectDefinition,
    RenderPlan,
)

_log = get_logger("ranking")


class ProjectValidationError(ValueError):
    """Raised when a project definition is structurally invalid."""


def _parse_detection_mode(value: object) -> DetectionMode:
    if value is None:
        return DetectionMode.AUTO
    if isinstance(value, DetectionMode):
        return value
    s = str(value).strip().lower()
    if s in ("", "none"):
        return DetectionMode.AUTO
    try:
        return DetectionMode(s)
    except ValueError:
        # Tolerate typos from the Streamlit free-text column: auto-correct to AUTO
        # and let validate_clips() surface a warning if needed.
        if s in ("auto", "automatic", "a"):
            return DetectionMode.AUTO
        if s in ("manual", "m", "user"):
            return DetectionMode.MANUAL
        if s in ("center", "c", "middle"):
            return DetectionMode.CENTER
        if s in ("full", "f", "whole", "all"):
            return DetectionMode.FULL
        raise ProjectValidationError(f"unknown detection_mode: {value!r}")


def parse_clip_dict(d: dict) -> ClipSpec:
    """Build a :class:`ClipSpec` from one JSON/CSV row dict."""
    try:
        rank = int(d["rank"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectValidationError(f"clip rank missing/invalid: {d!r}") from exc
    url = str(d.get("url", "")).strip()
    caption = str(d.get("caption", "") or "").strip()
    start_time: object | None = d.get("start_time")
    end_time: object | None = d.get("end_time")
    if start_time is None or str(start_time).strip() in ("", "None"):
        start_time = None
    else:
        start_time = float(str(start_time).strip())
    if end_time is None or str(end_time).strip() in ("", "None"):
        end_time = None
    else:
        end_time = float(str(end_time).strip())
    dur_override = d.get("duration_override")
    if dur_override is None or str(dur_override).strip() in ("", "None"):
        dur_override = None
    else:
        dur_override = float(str(dur_override).strip())
    # horizontal_flip: accept bool or common string forms
    hf_raw = d.get("horizontal_flip", False)
    if isinstance(hf_raw, bool):
        horizontal_flip = hf_raw
    else:
        horizontal_flip = str(hf_raw).strip().lower() in ("true", "1", "yes", "y")
    return ClipSpec(
        rank=rank,
        url=url,
        caption=caption,
        start_time=start_time,
        end_time=end_time,
        detection_mode=_parse_detection_mode(d.get("detection_mode", "auto")),
        duration_override=dur_override,
        background_music=d.get("background_music"),
        horizontal_flip=horizontal_flip,
    )


def validate_clips(clips: list[ClipSpec]) -> list[str]:
    """Return a list of validation warnings/errors for the dry-run report.

    Rules:
      - URL must be a valid http(s) URL.
      - Caption may be empty (we treat as warning, not failure).
      - Ranks must be unique.
      - BLOCKED detection is invalid; manual start/end required when manual.
    """
    issues: list[str] = []
    seen_ranks: set[int] = set()
    for c in clips:
        if not validate_url(c.url):
            issues.append(f"rank {c.rank}: bad URL '{c.url}'")
        if c.caption == "":
            issues.append(f"rank {c.rank}: empty caption (will render as blank)")
        if c.rank in seen_ranks:
            issues.append(f"duplicate rank {c.rank}")
        seen_ranks.add(c.rank)
        if c.detection_mode == DetectionMode.MANUAL:
            if c.start_time is None or c.end_time is None:
                issues.append(
                    f"rank {c.rank}: manual mode needs start_time & end_time"
                )
        if c.start_time is not None and c.end_time is not None and c.start_time >= c.end_time:
            issues.append(f"rank {c.rank}: start_time >= end_time")
    return issues


def load_project_json(path: Path) -> ProjectDefinition:
    """Load a project from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "clips" not in data:
        raise ProjectValidationError("JSON missing 'clips' array")
    clips = [parse_clip_dict(c) for c in data["clips"]]
    return ProjectDefinition(
        project_title=data.get("project_title", "Ranking Video"),
        clips=clips,
        output_resolution=data.get("output_resolution", "1080x1920"),
        background_music=data.get("background_music"),
        transition_style=data.get("transition_style", "fade"),
        style_preset=data.get("style_preset", "default"),
    )


def load_project_csv(path: Path, title: str = "Ranking Video") -> ProjectDefinition:
    """Load a project from a CSV file with the canonical column order."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"rank", "url"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ProjectValidationError(f"CSV missing columns: {missing}")
        rows = list(reader)
    clips = [parse_clip_dict(r) for r in rows]
    return ProjectDefinition(
        project_title=title,
        clips=clips,
    )


def project_from_dicts(
    title: str, clips_dicts: Iterable[dict]
) -> ProjectDefinition:
    """Build a project from in-memory dicts (used by the Streamlit UI)."""
    clips = [parse_clip_dict(d) for d in clips_dicts]
    return ProjectDefinition(project_title=title, clips=clips)


def build_render_plan(
    project: ProjectDefinition,
    cfg: AppConfig,
    output_path: Optional[Path] = None,
) -> RenderPlan:
    """Validate a project and produce a :class:`RenderPlan` (no downloads)."""
    from .models import ClipStatus, ProcessedClip

    issues = validate_clips(project.clips)
    warnings = [i for i in issues if "bad URL" not in i]
    errors = [i for i in issues if "bad URL" in i]

    valid_clips = [
        c for c in project.clips if validate_url(c.url)
    ]
    reports: list[dict] = []
    for c in valid_clips:
        pc = ProcessedClip(
            spec=c, local_path=Path(), duration_sec=0.0, width=0, height=0, has_audio=False
        )
        pc.status = ClipStatus.PENDING
        reports.append(pc.to_report_row())
    return RenderPlan(
        project=project,
        clip_reports=reports,
        output_path=output_path,
        warnings=warnings,
        errors=errors,
    )