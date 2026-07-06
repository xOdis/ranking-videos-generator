"""Tests for the detector heuristic helpers (no network/ffmpeg needed)."""
from __future__ import annotations

from pathlib import Path

from src.config_schema import AppConfig, DetectorConfig
from src.detector import _center_segment, _score_windows, _normalise
from src.models import (
    ClipSpec,
    ClipStatus,
    DetectionMode,
    ProcessedClip,
    SegmentPlan,
)


def test_center_segment_full_when_short():
    plan = _center_segment(duration=3.0, target=6.0)
    assert isinstance(plan, SegmentPlan)
    assert plan.start_time == 0.0
    assert plan.end_time == 3.0


def test_center_segment_centered():
    plan = _center_segment(duration=20.0, target=6.0)
    assert plan.start_time == 7.0 and plan.end_time == 13.0


def test_normalise():
    assert _normalise([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]
    assert _normalise([]) == []


def test_score_windows_single():
    cfg = AppConfig.from_env().detector
    scores = _score_windows([], [], duration=4.0, target=6.0, dcfg=cfg)
    assert len(scores) == 1
    assert scores[0].start == 0.0


def test_detector_center_mode_short_clip():
    spec = ClipSpec(rank=1, url="http://x.com/v.mp4", detection_mode=DetectionMode.CENTER)
    clip = ProcessedClip(
        spec=spec, local_path=Path("x"), duration_sec=4.0, width=720, height=1280, has_audio=False
    )
    from src.detector import detect_best_segment
    cfg = AppConfig.from_env()
    plan = detect_best_segment(clip, cfg)
    assert plan.mode == DetectionMode.CENTER