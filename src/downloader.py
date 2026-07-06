"""Source media acquisition — yt-dlp for hosted URLs, direct HTTP otherwise.

The downloader always validates the produced file with ffprobe so a corrupted
download surfaces as a controlled ``ClipStatus.FAILED`` rather than crashing the
render half-way through.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config_schema import AppConfig
from .ffmpeg_utils import is_url, probe_media, ffmpeg_bin
from .logging_utils import get_logger
from .models import ClipSpec, ClipStatus, ProcessedClip

_log = get_logger("downloader")


class DownloadError(RuntimeError):
    """Raised when a clip cannot be acquired."""


def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _is_ytdlp_url(url: str) -> bool:
    """Heuristic: YouTube / TikTok / Instagram / Twitter / yt-dlp hosts.

    Anything that is *not* a direct media file URL (no .mp4/.webm/.mov suffix)
    is routed through yt-dlp, which supports hundreds of sites.
    """
    path = urlparse(url).path.lower()
    direct_exts = (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".ts")
    if any(path.endswith(ext) for ext in direct_exts):
        return False
    host = (urlparse(url).hostname or "").lower()
    return bool(host)


def _direct_http_download(url: str, dst: Path) -> None:
    """Download a direct media URL via requests streaming."""
    import requests

    _log.info("HTTP download %s -> %s", url, dst)
    headers = {"User-Agent": "ranking-videos-generator/1.0"}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    fh.write(chunk)


def _yt_dlp_download(url: str, dst: Path, cfg: "AppConfig") -> None:
    """Download via yt-dlp (best mp4 up to 1080p).

    Passes our configured FFmpeg location to yt-dlp so it can merge split
    video/audio streams (YouTube/TikTok serve them separately) even when
    ffmpeg is not on the system PATH.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise DownloadError("yt-dlp is required for this URL but is not installed") from exc

    ffmpeg_dir = Path(cfg.ffmpeg_bin).parent if cfg.ffmpeg_bin else ""
    _log.info("yt-dlp download %s (ffmpeg_dir=%s)", url, ffmpeg_dir or "PATH")
    tmp = dst.with_suffix(".part")
    opts: dict = {
        "outtmpl": str(tmp),
        "format": "best[ext=mp4]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if ffmpeg_dir and Path(ffmpeg_dir).exists():
        opts["ffmpeg_location"] = str(ffmpeg_dir)
    # Prefer a single pre-merged progressive stream when available to avoid
    # needing ffmpeg at all; falls back to merging if needed.
    opts["format"] = (
        "best[ext=mp4][vcodec!=none][acodec!=none]/"
        "best[ext=mp4]/bestvideo*+bestaudio/best"
    )
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = (info or {}).get("title", "clip")
        _log.debug("yt-dlp got '%s'", title)
    # yt-dlp may append an extension; locate the produced file.
    produced = next(tmp.parent.glob(tmp.stem + "*"), None)
    if produced is None or not produced.exists():
        raise DownloadError(f"yt-dlp produced no file for {url}")
    shutil.move(str(produced), str(dst))


def validate_url(url: str) -> bool:
    """Public URL validator used by ranking/UI modules."""
    if not url or not isinstance(url, str):
        return False
    return is_url(url)


def download_clip(spec: ClipSpec, cfg: AppConfig) -> ProcessedClip:
    """Download a single clip and probe it. Returns a :class:`ProcessedClip`.

    On any failure the returned clip has ``status`` set to ``FAILED`` and an error
    message, instead of raising — letting the pipeline continue with other clips.
    """
    clip = ProcessedClip(
        spec=spec, local_path=Path(), duration_sec=0.0, width=0, height=0, has_audio=False
    )
    try:
        if not validate_url(spec.url):
            raise ValueError("invalid URL")
        cfg.paths.downloads_dir.mkdir(parents=True, exist_ok=True)
        ext = ".mp4"
        fname = f"{_hash_url(spec.url)}{ext}"
        dst = cfg.paths.downloads_dir / fname
        if not dst.exists() or dst.stat().st_size == 0:
            if _is_ytdlp_url(spec.url):
                _yt_dlp_download(spec.url, dst, cfg)
            else:
                _direct_http_download(spec.url, dst)
        info = probe_media(dst, cfg)
        if info.duration_sec <= 0:
            raise DownloadError("zero duration media")
        clip.local_path = dst
        clip.duration_sec = info.duration_sec
        clip.width = info.width
        clip.height = info.height
        clip.has_audio = info.has_audio
        clip.status = ClipStatus.DOWNLOADED
        _log.info(
            "OK %s dur=%.2f %dx%d audio=%s",
            spec.url, info.duration_sec, info.width, info.height, info.has_audio,
        )
    except Exception as exc:  # noqa: BLE001
        clip.status = ClipStatus.FAILED
        # yt-dlp wraps the real cause; surface the full chain for diagnosis
        cause = exc.__cause__ or exc.__context__
        detail = f"{type(exc).__name__}: {exc}"
        if cause and str(cause) not in detail:
            detail += f" | caused by: {type(cause).__name__}: {cause}"
        clip.error = detail[:400]
        _log.warning("failed clip rank=%s url=%s -> %s", spec.rank, spec.url, clip.error)
    return clip


def download_all(specs: list[ClipSpec], cfg: AppConfig):
    """Download a batch of clips sequentially (Windows-friendly; no thread pools)."""
    return [download_clip(s, cfg) for s in specs]