"""Unit tests for ranking parsing & validation (no network, no ffmpeg)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models import DetectionMode, ProjectDefinition
from src.ranking import (
    parse_clip_dict,
    validate_clips,
    load_project_json,
    load_project_csv,
    build_render_plan,
)
from src.config_schema import AppConfig


def test_parse_clip_dict_defaults():
    clip = parse_clip_dict({"rank": 3, "url": "https://x.com/v.mp4"})
    assert clip.rank == 3
    assert clip.detection_mode == DetectionMode.AUTO
    assert clip.start_time is None and clip.end_time is None


def test_parse_clip_invalid_rank():
    with pytest.raises(Exception):
        parse_clip_dict({"rank": "abc", "url": "https://x.com/v.mp4"})


def test_parse_clip_bad_mode():
    with pytest.raises(Exception):
        parse_clip_dict({"rank": 1, "url": "https://x.com/v.mp4", "detection_mode": "bogus"})


def test_validate_bad_url():
    clips = [
        parse_clip_dict({"rank": 1, "url": "ftp://nope.com/v.mp4"}),
        parse_clip_dict({"rank": 2, "url": "http://ok.com/v.mp4", "caption": "x"}),
    ]
    issues = validate_clips(clips)
    assert any("bad URL" in i for i in issues)


def test_validate_duplicate_ranks():
    clips = [
        parse_clip_dict({"rank": 1, "url": "http://a.com/v.mp4", "caption": "x"}),
        parse_clip_dict({"rank": 1, "url": "http://b.com/v.mp4", "caption": "y"}),
    ]
    issues = validate_clips(clips)
    assert any("duplicate" in i for i in issues)


def test_manual_mode_requires_times():
    clips = [
        parse_clip_dict(
            {"rank": 1, "url": "http://a.com/v.mp4", "detection_mode": "manual"}
        )
    ]
    issues = validate_clips(clips)
    assert any("manual mode" in i for i in issues)


def test_load_project_json(tmp_path: Path):
    data = {
        "project_title": "Test",
        "clips": [
            {"rank": 2, "url": "https://x.com/a.mp4", "caption": "hi"},
            {"rank": 1, "url": "https://x.com/b.mp4", "caption": "yo"},
        ],
    }
    p = tmp_path / "proj.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    proj = load_project_json(p)
    assert proj.project_title == "Test"
    assert len(proj.clips) == 2
    ranked = proj.sorted_by_rank()
    assert ranked[0].rank == 2 and ranked[1].rank == 1


def test_load_project_csv(tmp_path: Path):
    csv_text = "rank,url,caption,start_time,end_time,detection_mode\n"
    csv_text += "1,https://x.com/v.mp4,bad,1.0,3.0,manual\n"
    csv_text += "2,https://y.com/v.mp4,ok,,  ,auto\n"
    p = tmp_path / "p.csv"
    p.write_text(csv_text, encoding="utf-8")
    proj = load_project_csv(p, title="CSV Title")
    assert proj.project_title == "CSV Title"
    assert len(proj.clips) == 2
    assert proj.clips[0].start_time == 1.0
    assert proj.clips[1].start_time is None


def test_build_render_plan():
    clips = [
        parse_clip_dict({"rank": 1, "url": "http://a.com/v.mp4", "caption": "x"}),
        parse_clip_dict({"rank": 1, "url": "bad", "caption": "y"}),
    ]
    proj = ProjectDefinition(project_title="t", clips=clips)
    cfg = AppConfig.from_env()
    plan = build_render_plan(proj, cfg)
    assert plan.output_path is None