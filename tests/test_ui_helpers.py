"""Test for ui_helpers parsing (no Streamlit runtime needed)."""
from __future__ import annotations

from src.ui_helpers import (
    rows_to_project,
    import_csv_bytes,
    import_json_bytes,
    project_to_rows,
    validation_status,
)


def test_rows_to_project_skips_empty():
    rows = [
        {"rank": 1, "url": "https://x.com/v.mp4", "caption": "hi",
         "start_time": "", "end_time": "", "detection_mode": "auto"},
        {"rank": 2, "url": "", "caption": "", "start_time": "",
         "end_time": "", "detection_mode": "auto"},
    ]
    proj = rows_to_project("t", rows)
    assert len(proj.clips) == 1
    assert proj.clips[0].rank == 1


def test_import_csv_bytes():
    raw = (
        b"rank,url,caption,start_time,end_time,detection_mode\n"
        b"2,https://x.com/v.mp4,hi,1.0,3.0,manual\n"
        b"1,https://y.com/v.mp4,yo,,  ,auto\n"
    )
    proj = import_csv_bytes(raw)
    assert len(proj.clips) == 2


def test_import_json_bytes():
    raw = (
        b'{"project_title":"x","clips":['
        b'{"rank":1,"url":"https://a.com/v.mp4","caption":"c"}]}'
    )
    proj = import_json_bytes(raw)
    assert proj.project_title == "x"
    assert len(proj.clips) == 1


def test_project_to_rows_roundtrip():
    rows = [
        {"rank": 1, "url": "https://x.com/v.mp4", "caption": "hi",
         "start_time": 1.0, "end_time": 3.0, "detection_mode": "manual"},
    ]
    proj = rows_to_project("t", rows)
    out = project_to_rows(proj)
    assert out[0]["rank"] == 1
    assert out[0]["detection_mode"] == "manual"


def test_validation_status_split():
    rows = [
        {"rank": 1, "url": "bad", "caption": "", "start_time": "",
         "end_time": "", "detection_mode": "auto"},
    ]
    proj = rows_to_project("t", rows)
    errors, warnings = validation_status(proj)
    assert errors  # bad URL -> error
    assert warnings  # empty caption -> warning