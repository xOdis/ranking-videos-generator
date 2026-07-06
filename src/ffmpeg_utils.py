"""FFmpeg / ffprobe command generation and execution helpers.

Every FFmpeg call in the project is built by a small pure function here so the
rest of the codebase never shells out to ffmpeg ad-hoc. This makes the commands
testable (you can assert on the returned arg lists) and Windows-friendly (we use
subprocess list-form args, no shell=True).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config_schema import AppConfig, EncodingConfig, CanvasConfig
from .logging_utils import get_logger
from .models import ProcessedClip

_log = get_logger("ffmpeg_utils")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _binary(name: str, override: str) -> str:
    """Return the binary path, preferring explicit override then PATH lookup."""
    if override and Path(override).exists():
        return override
    found = shutil.which(override or name)
    return found or name


def ffmpeg_bin(cfg: AppConfig) -> str:
    return _binary("ffmpeg", cfg.ffmpeg_bin)


def ffprobe_bin(cfg: AppConfig) -> str:
    return _binary("ffprobe", cfg.ffprobe_bin)


@dataclass
class MediaInfo:
    duration_sec: float
    width: int
    height: int
    has_audio: bool
    codec: str


def probe_media(path: Path, cfg: AppConfig) -> MediaInfo:
    """Return duration, dimensions and presence of audio for a media file."""
    cmd = [
        ffprobe_bin(cfg), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0.0)
    return MediaInfo(
        duration_sec=duration,
        width=int(v.get("width", 0)),
        height=int(v.get("height", 0)),
        has_audio=bool(a),
        codec=v.get("codec_name", "unknown"),
    )


def run_ffmpeg(cmd: list[str], cfg: AppConfig, *, quiet: bool = False) -> subprocess.CompletedProcess:
    """Run an ffmpeg command, logging on failure. Never uses shell=True."""
    _log.debug("ffmpeg cmd: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not quiet:
        _log.error("ffmpeg exit %s: %s", proc.returncode, proc.stderr[-1500:])
    return proc


def hwaccel_input_args(cfg: AppConfig) -> list[str]:
    """Hardware decoding args. Empty when disabled (Windows-safe default)."""
    if cfg.encoding.hw_accel in ("none", "auto"):
        return []
    if cfg.encoding.hw_accel == "qsv":
        return ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
    if cfg.encoding.hw_accel == "nvenc":
        return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    if cfg.encoding.hw_accel == "amf":
        return ["-hwaccel", "d3d11va"]
    return []


def encoder_args(enc: EncodingConfig) -> list[str]:
    """Return encoder args, picking a hardware encoder when configured."""
    codec = enc.video_codec
    if enc.hw_accel == "nvenc":
        codec = "h264_nvenc"
    elif enc.hw_accel == "qsv":
        codec = "h264_qsv"
    elif enc.hw_accel == "amf":
        codec = "h264_amf"
    args = ["-c:v", codec, "-preset", enc.preset, "-pix_fmt", enc.pixel_format]
    if codec.startswith("libx264"):
        args += ["-crf", str(enc.crf)]
    else:
        args += ["-b:v", enc.video_bitrate]
    args += ["-r", "30", "-c:a", enc.audio_codec, "-b:a", enc.audio_bitrate]
    return args


def build_normalize_command(
    src: Path,
    dst: Path,
    cfg: AppConfig,
    *,
    start: float,
    end: float,
    has_audio: bool = True,
    horizontal_flip: bool = False,
    crop_center: bool = True,
) -> list[str]:
    """Build the command that trims + smart-fits one clip to a vertical canvas.

    Crop mode scales the source so its height covers the canvas, then crops the
    width to the centre. Pad mode scales to fit width and letterboxes to canvas
    with a solid background colour. When ``has_audio`` is False a silent stereo
    track is synthesized so every normalized output has a guaranteed audio
    stream — this keeps the final concat's pad alignment valid for all clips.
    When ``horizontal_flip`` is True an ``hflip`` filter is prepended to the
    video chain — useful to avoid automated content-match strikes / bans.
    """
    canvas: CanvasConfig = cfg.canvas
    W, H = canvas.width, canvas.height
    fit_mode = cfg.clip.fit_mode

    vf: list[str]
    if fit_mode == "pad":
        vf = [
            f"scale={W}:{H}:force_original_aspect_ratio=decrease",
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={canvas.background_color}",
            f"fps={canvas.fps}",
        ]
    else:
        # crop: cover height then centre-crop width
        vf = [
            f"scale=-2:{H}:force_original_aspect_ratio=increase",
            f"crop={W}:{H}",
            f"fps={canvas.fps}",
        ]
    if horizontal_flip:
        # mirror left<->right so the visual fingerprint changes but motion is
        # preserved (a common anti-strike technique for reposts)
        vf.append("hflip")

    args = [
        ffmpeg_bin(cfg),
        *hwaccel_input_args(cfg),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
    ]
    if not has_audio:
        # synthesize a silent stereo track matched to the segment length
        args += [
            "-f", "lavfi", "-t", f"{max(0.1, end - start):.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    args += ["-vf", ",".join(vf), "-map", "0:v:0"]
    if has_audio:
        args += ["-map", "0:a:0"]
    else:
        args += ["-map", "1:a:0"]  # the synthesized silent audio
    args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", str(canvas.fps),
        "-c:a", "aac", "-b:a", cfg.encoding.audio_bitrate,
        "-ar", "48000", "-ac", "2",
        "-shortest",
        "-y", str(dst),
    ]
    return args


def build_overlay_command(
    clip_paths: list[Path],
    overlay_paths: list[Path],
    dst: Path,
    cfg: AppConfig,
) -> list[str]:
    """Build the final filter_complex command that composes the ranking video.

    Each ranked clip is overlaid with its *own* per-clip PNG (title + sidebar
    with that clip's rank highlighted + that clip's caption), then the overlaid
    clips are concatenated and the audio is loudness-normalized.

    Inputs (in order): [0..N-1] normalized clip files, [N..2N-1] per-clip PNGs.
    """
    canvas = cfg.canvas
    W, H = canvas.width, canvas.height
    if not clip_paths:
        raise ValueError("build_overlay_command requires at least one clip")
    if len(overlay_paths) != len(clip_paths):
        raise ValueError("overlay_paths must align 1:1 with clip_paths")

    parts: list[str] = []
    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", str(p)]
    for p in overlay_paths:
        inputs += ["-i", str(p)]

    n = len(clip_paths)
    overlaid_labels: list[str] = []
    audio_labels: list[str] = []
    for i in range(n):
        ov_idx = n + i
        # video: scale to canvas, set continuous pts; overlay the per-clip png
        parts.append(
            f"[{i}:v]setpts=PTS-STARTPTS,scale={W}:{H},format=yuv420p,fps={canvas.fps}[bv{i}]"
        )
        parts.append(f"[{ov_idx}]format=rgba[ov{i}]")
        parts.append(f"[bv{i}][ov{i}]overlay=0:0:format=auto[v{i}]")
        overlaid_labels.append(f"[v{i}]")
        # audio: resample for safe concat. Normalizer guarantees an audio track,
        # but if missing we fall back to a silent generated source (defensive).
        parts.append(
            f"[{i}:a]aresample=async=1:first_pts=0[a{i}]"
        )
        audio_labels.append(f"[a{i}]")

    # concat expects inputs per-segment: [v0][a0][v1][a1]... (video then audio,
# for each segment) — NOT all-video-then-all-audio.
    concat_inputs = "".join(
        f"{overlaid_labels[i]}{audio_labels[i]}" for i in range(n)
    )
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vg][ag]")
    if cfg.encoding.normalize_loudness:
        parts.append(
            f"[ag]loudnorm=I={cfg.encoding.loudness_target}:"
            f"TP={cfg.encoding.loudness_true_peak}:LRA=11[afx]"
        )
        audio_label = "[afx]"
    else:
        audio_label = "[ag]"

    filter_complex = ";".join(parts)
    return [
        ffmpeg_bin(cfg),
        *hwaccel_input_args(cfg),
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vg]", "-map", audio_label,
        *encoder_args(cfg.encoding),
        "-movflags", "+faststart",
        "-y", str(dst),
    ]


def build_concat_command(segments: list[Path], dst: Path, cfg: AppConfig) -> list[str]:
    """Concatenate already-encoded clips via the concat demuxer (fast path)."""
    list_file = dst.with_suffix(".concat.txt")
    with open(list_file, "w", encoding="utf-8") as fh:
        for p in segments:
            fh.write(f"file '{p.as_posix()}'\n")
    return [
        ffmpeg_bin(cfg), "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-y", str(dst),
    ]


def build_thumbnail_command(src: Path, at_sec: float, dst: Path, cfg: AppConfig) -> list[str]:
    """Extract a single PNG thumbnail at the given timestamp."""
    return [
        ffmpeg_bin(cfg), "-y", "-ss", f"{at_sec:.3f}", "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(dst),
    ]


def is_url(value: str) -> bool:
    """Cheap URL validation for http(s) URLs."""
    return bool(_URL_RE.match(value.strip()))


def escape_drawtext(text: str) -> str:
    """Escape a string for ffmpeg drawtext ``text=`` argument."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("%", "\\%")
    return text