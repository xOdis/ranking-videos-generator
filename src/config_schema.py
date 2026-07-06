"""Central configuration schema for the Ranking Videos Generator.

All configurable values live here. Other modules import from this module rather
than scattering magic numbers around the codebase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


def load_dotenv(env_path: Path | None = None) -> None:
    """Load a ``.env`` file into ``os.environ`` (minimal, no dependency).

    Skips blanks and comments. Does NOT override values already set in the
    real environment so explicit CLI/shell vars win.
    """
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


load_dotenv()


@dataclass(frozen=True)
class PathConfig:
    """Filesystem layout used by the pipeline."""

    project_root: Path = Path(__file__).resolve().parent.parent
    downloads_dir: Path = Path("temp/downloads")
    normalized_dir: Path = Path("temp/normalized")
    overlays_dir: Path = Path("temp/overlays")
    output_dir: Path = Path("output")
    fonts_dir: Path = Path("assets/fonts")
    sfx_dir: Path = Path("assets/sfx")
    overlay_assets_dir: Path = Path("assets/overlays")

    def resolve(self) -> "PathConfig":
        """Return a copy with all paths resolved relative to the project root."""
        root = self.project_root
        return PathConfig(
            project_root=root,
            downloads_dir=root / self.downloads_dir,
            normalized_dir=root / self.normalized_dir,
            overlays_dir=root / self.overlays_dir,
            output_dir=root / self.output_dir,
            fonts_dir=root / self.fonts_dir,
            sfx_dir=root / self.sfx_dir,
            overlay_assets_dir=root / self.overlay_assets_dir,
        )


@dataclass(frozen=True)
class CanvasConfig:
    """Output video geometry and typography metrics."""

    width: int = 1080
    height: int = 1920
    fps: int = 30
    title_font_size: int = 56
    rank_font_size: int = 64
    caption_font_size: int = 64
    title_height: int = 150
    sidebar_width: int = 380
    caption_area_height: int = 180
    safe_margin_top: int = 90
    safe_margin_bottom: int = 120
    safe_margin_side: int = 60
    title_margin_x: int = 90
    background_color: str = "0x0A0A12"
    title_color: str = "white"
    rank_color: str = "0xB0B0C0"
    active_rank_color: str = "0xFFD23F"
    caption_color: str = "white"
    stroke_color: str = "black"
    stroke_width: int = 6
    font_name: str = "Arial-Bold"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True)
class EncodingConfig:
    """FFmpeg encoder settings."""

    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    video_bitrate: str = "8M"
    preset: str = "medium"
    crf: int = 18
    pixel_format: str = "yuv420p"
    hw_accel: Literal["auto", "none", "qsv", "nvenc", "amf", "videotoolbox"] = "none"
    normalize_loudness: bool = True
    loudness_target: float = -16.0
    loudness_true_peak: float = -1.5


@dataclass(frozen=True)
class ClipConfig:
    """Per-clip defaults and detection tuning."""

    target_segment_seconds: float = 6.0
    max_segment_seconds: float = 12.0
    min_segment_seconds: float = 3.0
    target_fps: int = 30
    sample_frames_for_detection: int = 120
    motion_threshold: float = 0.015
    audio_window_seconds: float = 0.5
    transition_seconds: float = 0.4
    transition_style: Literal["fade", "slide", "none"] = "fade"
    fit_mode: Literal["crop", "pad"] = "crop"


@dataclass(frozen=True)
class DetectorConfig:
    """Detection pipeline tuning used by :mod:`src.detector`."""

    audio_weight: float = 0.45
    motion_weight: float = 0.35
    scene_energy_weight: float = 0.20
    min_confidence: float = 0.15
    window_count: int = 24
    enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    """Top level application configuration."""

    paths: PathConfig = field(default_factory=PathConfig)
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    encoding: EncodingConfig = field(default_factory=EncodingConfig)
    clip: ClipConfig = field(default_factory=ClipConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    max_workers: int = 3
    ffprobe_bin: str = ""
    ffmpeg_bin: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build an :class:`AppConfig` from environment variables (``.env``)."""
        cfg = cls(paths=PathConfig().resolve())
        valid_hw = ("auto", "none", "qsv", "nvenc", "amf", "videotoolbox")
        hw = os.getenv("HW_ACCEL", cfg.encoding.hw_accel).lower()
        if hw not in valid_hw:
            hw = cfg.encoding.hw_accel
        return AppConfig(
            paths=cfg.paths,
            ffprobe_bin=os.getenv("FFPROBE_BIN", "") or cfg.ffprobe_bin or "ffprobe",
            ffmpeg_bin=os.getenv("FFMPEG_BIN", "") or cfg.ffmpeg_bin or "ffmpeg",
            max_workers=int(os.getenv("MAX_WORKERS", str(cfg.max_workers)) or cfg.max_workers),
            log_level=os.getenv("LOG_LEVEL", cfg.log_level),
            encoding=EncodingConfig(hw_accel=hw),  # type: ignore[arg-type]
        )


DEFAULT_CONFIG = AppConfig.from_env()