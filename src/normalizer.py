"""Media normalization — trims each clip to its chosen segment and smart-fits
it to the 9:16 canvas using an FFmpeg pipeline (lossless intermediate).

The normalizer consumes a :class:`ProcessedClip` whose ``segment`` has already
been chosen (manual or detected) and writes a normalized, vertically-cropped
intermediate file that the compositor later overlays.
"""
from __future__ import annotations

from pathlib import Path

from .config_schema import AppConfig
from .ffmpeg_utils import build_normalize_command, run_ffmpeg
from .logging_utils import get_logger
from .models import ClipStatus, ProcessedClip, SegmentPlan

_log = get_logger("normalizer")


def normalize_clip(clip: ProcessedClip, cfg: AppConfig) -> ProcessedClip:
    """Trim + smart-fit a single clip to its segment; updates ``normalized_path``.

    Marks the clip ``FAILED`` (with a reason) rather than raising when something
    goes wrong, so the pipeline can continue with the remaining clips.
    """
    if clip.status == ClipStatus.FAILED or clip.segment is None or not clip.local_path.exists():
        if clip.status != ClipStatus.FAILED:
            clip.status = ClipStatus.FAILED
            clip.error = clip.error or "no segment or source file missing"
        return clip

    seg: SegmentPlan = clip.segment
    try:
        cfg.paths.normalized_dir.mkdir(parents=True, exist_ok=True)
        dst = cfg.paths.normalized_dir / f"{clip.local_path.stem}_r{clip.spec.rank}_norm.mp4"
        start = max(0.0, min(seg.start_time, clip.duration_sec - 0.1))
        end = min(seg.end_time, clip.duration_sec) if seg.end_time else min(seg.end_time, clip.duration_sec)
        end = max(start + cfg.clip.min_segment_seconds, end)
        cmd = build_normalize_command(
            clip.local_path, dst, cfg,
            start=start, end=end, has_audio=clip.has_audio,
            horizontal_flip=clip.spec.horizontal_flip,
        )
        proc = run_ffmpeg(cmd, cfg)
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(f"ffmpeg normalize failed (rc={proc.returncode})")
        clip.normalized_path = dst
        clip.status = ClipStatus.NORMALIZED
        _log.info("normalized rank=%s -> %s", clip.spec.rank, dst.name)
    except Exception as exc:  # noqa: BLE001
        clip.status = ClipStatus.FAILED
        clip.error = f"normalizer: {type(exc).__name__}: {exc}"
        _log.warning("normalizer failed rank=%s -> %s", clip.spec.rank, clip.error)
    return clip


def normalize_all(clips: list[ProcessedClip], cfg: AppConfig) -> list[ProcessedClip]:
    """Normalize every clip in list. Failed clips are left in FAILED state."""
    return [normalize_clip(c, cfg) for c in clips]