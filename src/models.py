"""Domain models for the Ranking Videos Generator pipeline.

These dataclasses are the single source of truth that flows between services
(downloader -> normalizer -> detector -> compositor -> renderer).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class DetectionMode(str, Enum):
    """How the best moment of a clip is chosen."""

    AUTO = "auto"
    MANUAL = "manual"
    CENTER = "center"
    FULL = "full"


class ClipStatus(str, Enum):
    """Lifecycle status of a processed clip."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    NORMALIZED = "normalized"
    ANALYZED = "analyzed"
    RENDERED = "rendered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SegmentPlan:
    """The chosen segment within a clip and why it was chosen."""

    start_time: float
    end_time: float
    confidence: float
    mode: DetectionMode
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class ClipSpec:
    """A single ranked clip as read from JSON/CSV (before any media exists)."""

    rank: int
    url: str
    caption: str = ""
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    detection_mode: DetectionMode = DetectionMode.AUTO
    duration_override: Optional[float] = None
    background_music: Optional[str] = None
    horizontal_flip: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["detection_mode"] = self.detection_mode.value
        return d


@dataclass
class ProcessedClip:
    """A clip that has been downloaded/normalized with a chosen segment plan."""

    spec: ClipSpec
    local_path: Path
    duration_sec: float
    width: int
    height: int
    has_audio: bool
    normalized_path: Optional[Path] = None
    segment: Optional[SegmentPlan] = None
    status: ClipStatus = ClipStatus.PENDING
    error: Optional[str] = None

    def to_report_row(self) -> dict:
        seg = self.segment
        return {
            "rank": self.spec.rank,
            "url": self.spec.url,
            "duration_src": round(self.duration_sec, 2),
            "start": round(seg.start_time, 2) if seg else None,
            "end": round(seg.end_time, 2) if seg else None,
            "confidence": round(seg.confidence, 3) if seg else None,
            "mode": seg.mode.value if seg else None,
            "reason": seg.reason if seg else "no segment",
            "status": self.status.value,
        }


@dataclass
class ProjectDefinition:
    """Top-level ranking project (parsed JSON/CSV + global settings)."""

    project_title: str
    clips: list[ClipSpec]
    output_resolution: str = "1080x1920"
    background_music: Optional[str] = None
    transition_style: str = "fade"
    style_preset: str = "default"

    def sorted_by_rank(self) -> list[ClipSpec]:
        """Return clips sorted by rank ascending so #1 plays last (countdown order)."""
        return sorted(self.clips, key=lambda c: c.rank, reverse=True)


@dataclass
class RenderPlan:
    """The full render plan printed in dry-run mode."""

    project: ProjectDefinition
    clip_reports: list[dict] = field(default_factory=list)
    output_path: Optional[Path] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Render Plan — {self.project.project_title}",
            f"Clips total: {len(self.project.clips)}",
            f"Valid clips: {len(self.clip_reports)} (failed excluded)",
            f"Output: {self.output_path}",
        ]
        if self.warnings:
            lines.append(f"Warnings: {len(self.warnings)}")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return "\n".join(lines)