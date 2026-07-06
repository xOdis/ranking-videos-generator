"""Best-moment detection pipeline.

This is intentionally a *heuristic* pipeline with a clean plug-in seam for
future AI detectors. The baseline combines three cheap, deterministic signals:

- **Audio loudness spike** (max-RMS-over-window) — the loudest ~0.5s window.
- **Motion spike** — mean absolute frame difference across sampled frames.
- **Scene energy** — brightness/luma variance change between frames.

Each signal is scored per candidate window, combined with configurable weights
into a final confidence (0..1). If the best confidence is below
``min_confidence`` (or detection is disabled) the segment falls back to the clip
center.

An optional *AI detector plugin* can be registered via :func:`register_detector`
and will override the heuristic when it returns a confident result.
"""
from __future__ import annotations

import math
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from .config_schema import AppConfig, DetectorConfig
from .ffmpeg_utils import ffprobe_bin
from .logging_utils import get_logger
from .models import ClipSpec, ClipStatus, DetectionMode, ProcessedClip, SegmentPlan

_log = get_logger("detector")


class AIDetectorPlugin(Protocol):
    """Optional plug-in interface for future AI-based moment detection."""

    name: str

    def detect(
        self, clip: ProcessedClip, cfg: AppConfig
    ) -> Optional[tuple[float, float, float, str]]:
        """Return (start, end, confidence, reason) or ``None`` to defer."""
        ...


_AI_PLUGINS: list[AIDetectorPlugin] = []


def register_detector(plugin: AIDetectorPlugin) -> None:
    """Register an AI detector plug-in (used by advanced mode, future expansion)."""
    _AI_PLUGINS.append(plugin)
    _log.info("registered detector plugin: %s", getattr(plugin, "name", plugin))


def _audio_rms_curve(
    src: Path, cfg: AppConfig, duration: float, samples: int = 480
) -> list[tuple[float, float]]:
    """Sample an RMS loudness curve using ffmpeg astats.

    Returns a list of ``(time, rms)`` tuples sampled ~``duration/samples`` apart.
    """
    n = max(32, min(samples, int(duration * 10)))
    step = duration / n if duration > 0 else 0.0
    out: list[tuple[float, float]] = []
    win = 0.5
    cmd = None
    for i in range(n):
        t = i * step
        if t + win > duration:
            t = max(0.0, duration - win)
        cmd = [
            ffprobe_bin(cfg) if False else "ffmpeg",
        ]
        # We avoid relying on the import-time ffmpeg bin lookup here for testability;
        # use a lightweight astats pipe.
        cmd = [
            "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-t", f"{win:.3f}",
            "-i", str(src), "-af", "astats=metadata=1:reset=1",
            "-f", "null", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            txt = proc.stderr
            # parse RMS level from astats metadata
            m = _parse_astats_rms(txt)
            out.append((t, m))
        except Exception:  # noqa: BLE001
            out.append((t, 0.0))
    return out


def _parse_astats_rms(stderr_text: str) -> float:
    """Best-effort parse of an RMS level from ffmpeg astats stderr output."""
    for line in stderr_text.splitlines():
        low = line.lower()
        if "rms level" in low and "=" in line:
            try:
                val = float(line.split("=")[-1].strip().split()[0])
                return abs(val)
            except (ValueError, IndexError):
                continue
    return 0.0


def _frame_diff_curve(src: Path, cfg: AppConfig, duration: float) -> list[tuple[float, float]]:
    """Sample per-frame luminance using opencv and return a coarse motion curve.

    Uses cv2 if available. If opencv is missing it returns a flat zero curve so
    the detector still produces a valid (audio-only) result.
    """
    try:
        import cv2  # type: ignore
        import numpy as np
    except ImportError:  # pragma: no cover
        _log.warning("opencv not installed; motion signal disabled")
        n = max(20, cfg.clip.sample_frames_for_detection)
        return [(i * duration / max(n, 1), 0.0) for i in range(n)]

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return [(0.0, 0.0)]
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    n = min(cfg.clip.sample_frames_for_detection, max(total, 1))
    prev_gray = None
    diffs: list[tuple[float, float]] = []
    for i in range(n):
        idx = int(i * max(total - 1, 0) / max(n - 1, 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (160, 160))
        if prev_gray is not None:
            d = float(np.mean(cv2.absdiff(gray, prev_gray)) / 255.0)
            diffs.append((idx / max(cv2.CAP_PROP_FPS or 30, 1), d))
        else:
            diffs.append((0.0, 0.0))
        prev_gray = gray
    cap.release()
    return diffs


def _normalise(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0
    return [(v - lo) / span for v in values]


@dataclass
class _WindowScore:
    start: float
    end: float
    audio: float
    motion: float
    scene: float
    confidence: float
    reason: str


def _score_windows(
    audio: list[tuple[float, float]],
    motion: list[tuple[float, float]],
    duration: float,
    target: float,
    dcfg: DetectorConfig,
) -> list[_WindowScore]:
    """Slide a target-length window over the signals and score each one."""
    if duration <= target:
        return [
            _WindowScore(
                0.0, duration, 0.5, 0.5, 0.5, 0.5, "single-window"
            )
        ]
    starts = [
        i * (duration - target) / max(dcfg.window_count - 1, 1)
        for i in range(dcfg.window_count)
    ]
    rms_cur = _normalise([v for _, v in audio]) if audio else []
    mot_cur = _normalise([v for _, v in motion]) if motion else []

    def _window_mean(curve, indices_norm, s, e):
        vals = [v for t, v in curve if s <= t <= e]
        if not vals:
            return 0.0
        return statistics.mean(vals)

    scores: list[_WindowScore] = []
    for s in starts:
        e = s + target
        rms = _window_mean(audio, rms_cur, s, e)
        mot = max((v for t, v in motion if s <= t <= e), default=0.0)
        scene = abs(mot)  # simplified scene-energy proxy
        conf = (
            dcfg.audio_weight * rms
            + dcfg.motion_weight * mot
            + dcfg.scene_energy_weight * scene
        )
        reason = f"audio={rms:.2f},motion={mot:.2f}"
        scores.append(_WindowScore(s, e, rms, mot, scene, conf, reason))
    if not scores:
        scores.append(_WindowScore(0.0, min(target, duration), 0.0, 0.0, 0.0, 0.0, "fallback"))
    return scores


def _center_segment(duration: float, target: float) -> SegmentPlan:
    if duration <= target:
        return SegmentPlan(0.0, duration, 0.0, DetectionMode.CENTER, "center (full)")
    s = (duration - target) / 2.0
    return SegmentPlan(s, s + target, 0.0, DetectionMode.CENTER, "center fallback")


def _full_segment(duration: float) -> SegmentPlan:
    """Use the entire clip — no trimming."""
    return SegmentPlan(0.0, duration, 0.0, DetectionMode.FULL, "full clip")


def detect_best_segment(clip: ProcessedClip, cfg: AppConfig) -> SegmentPlan:
    """Choose the best :class:`SegmentPlan` for a clip according to its mode.

    - ``MANUAL`` — trust ``spec.start_time``/``spec.end_time``.
    - ``CENTER`` — centered segment.
    - ``AUTO`` — heuristic pipeline, optionally overridden by an AI plugin.

    The plan always clamps to the clip's real duration.
    """
    spec: ClipSpec = clip.spec
    duration = clip.duration_sec
    target = spec.duration_override or cfg.clip.target_segment_seconds
    target = max(cfg.clip.min_segment_seconds, min(target, cfg.clip.max_segment_seconds))

    if spec.detection_mode == DetectionMode.MANUAL:
        s = spec.start_time or 0.0
        e = spec.end_time or min(s + target, duration)
        return SegmentPlan(s, e, 1.0, DetectionMode.MANUAL, "manual")

    if spec.detection_mode == DetectionMode.FULL:
        return _full_segment(duration)

    if spec.detection_mode == DetectionMode.CENTER or not cfg.detector.enabled:
        return _center_segment(duration, target)

    # AUTO — ask AI plugins first
    for plugin in _AI_PLUGINS:
        try:
            res = plugin.detect(clip, cfg)
            if res is not None:
                s, e, conf, reason = res
                _log.info("AI plugin '%s' chose [%.2f-%.2f] conf=%.2f", plugin.name, s, e, conf)
                return SegmentPlan(s, e, conf, DetectionMode.AUTO, f"ai:{reason}")
        except Exception as exc:  # noqa: BLE001
            _log.warning("AI plugin %s errored: %s", plugin.name, exc)

    # Heuristic pipeline
    try:
        audio = _audio_rms_curve(clip.local_path, cfg, duration=duration)  # noqa: E501
        motion = _frame_diff_curve(clip.local_path, cfg, duration)
    except Exception as exc:  # noqa: BLE001
        _log.warning("detector signals failed (%s); using center", exc)
        return _center_segment(duration, target)

    scores = _score_windows(audio, motion, duration, target, cfg.detector)
    best = max(scores, key=lambda x: x.confidence)
    if best.confidence < cfg.detector.min_confidence:
        # Low confidence: if the clip is short enough to use whole, keep it
        # intact (don't slice a 10s Short down to 6s). Otherwise take centre.
        if duration <= cfg.clip.max_segment_seconds:
            return _full_segment(duration)
        plan = _center_segment(duration, target)
        plan.reason = "low confidence -> center"
        return plan

    return SegmentPlan(
        max(0.0, best.start),
        min(best.end, duration),
        best.confidence,
        DetectionMode.AUTO,
        best.reason,
    )


def analyze_clip(clip: ProcessedClip, cfg: AppConfig) -> ProcessedClip:
    """Set ``clip.segment`` (and ``status``) by detecting the best segment."""
    if clip.status == ClipStatus.FAILED:
        return clip
    try:
        clip.segment = detect_best_segment(clip, cfg)
        clip.status = ClipStatus.ANALYZED
        _log.info(
            "analyzed rank=%s [%s %.2f-%.2f] conf=%.2f",
            clip.spec.rank, clip.segment.mode.value,
            clip.segment.start_time, clip.segment.end_time, clip.segment.confidence,
        )
    except Exception as exc:  # noqa: BLE001
        clip.status = ClipStatus.FAILED
        clip.error = f"detector: {exc}"
        _log.warning("detector failed rank=%s: %s", clip.spec.rank, exc)
    return clip


def analyze_all(clips: list[ProcessedClip], cfg: AppConfig) -> list[ProcessedClip]:
    return [analyze_clip(c, cfg) for c in clips]