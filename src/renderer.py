"""End-to-end renderer: orchestrates download -> analyze -> normalize -> overlay
-> final FFmpeg compose. Also provides the CLI entry point used for dry-run and
full render.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

from .config_schema import AppConfig
from .compositor import render_overlays
from .detector import analyze_all
from .downloader import download_all
from .ffmpeg_utils import build_overlay_command, run_ffmpeg
from .logging_utils import configure_logging, get_logger
from .models import ClipStatus, ProcessedClip, RenderPlan, ProjectDefinition
from .normalizer import normalize_all
from .ranking import (
    build_render_plan,
    load_project_csv,
    load_project_json,
    validate_clips,
)

_log = get_logger("renderer")


def _print_report(plan: RenderPlan) -> None:
    """Pretty-print the dry-run / segment analysis report."""
    print("\n" + "=" * 64)
    print(plan.summary())
    print("-" * 64)
    print("Validation issues:")
    if plan.errors:
        for e in plan.errors:
            print(f"  ERROR  {e}")
    if plan.warnings:
        for w in plan.warnings:
            print(f"  WARN   {w}")
    print("-" * 64)
    print("Segment analysis report:")
    print(f"{'rank':<6}{'start':<8}{'end':<8}{'conf':<8}{'mode':<10}{'reason'}")
    for row in plan.clip_reports:
        print(
            f"{row.get('rank'):<6}{str(row.get('start')):<8}"
            f"{str(row.get('end')):<8}{str(row.get('confidence')):<8}"
            f"{str(row.get('mode')):<10}{row.get('reason','')}"
        )
    print("=" * 64 + "\n")


def dry_run(project: ProjectDefinition, cfg: AppConfig) -> RenderPlan:
    """Validate + print the render plan without downloading or rendering."""
    plan = build_render_plan(project, cfg)
    issues = validate_clips(project.clips)
    plan.errors = [i for i in issues if "bad URL" in i]
    plan.warnings = [i for i in issues if "bad URL" not in i]
    _print_report(plan)
    valid_count = len(project.clips) - len(plan.errors)
    print(f"Dry run OK: {len(project.clips)} clips declared, {valid_count} valid.")
    return plan


def render_project(
    project: ProjectDefinition,
    cfg: AppConfig,
    output_path: Optional[Path] = None,
    *,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> Path:
    """Full end-to-end render. Returns the path to the final MP4.

    ``progress_cb`` is an optional ``callable(stage:str, current:int, total:int)``
    used by the Streamlit UI to update a progress bar.
    """
    cfg.paths.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.paths.downloads_dir.mkdir(parents=True, exist_ok=True)
    cfg.paths.normalized_dir.mkdir(parents=True, exist_ok=True)
    cfg.paths.overlays_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in project.project_title)
        output_path = cfg.paths.output_dir / f"{safe.strip() or 'ranking'}.mp4"

    total = len(project.clips)

    def _cb(stage: str, current: int) -> None:
        if progress_cb:
            progress_cb(stage, current, total)

    _cb("validation", 0)
    issues = validate_clips(project.clips)
    fatal = [i for i in issues if "bad URL" in i]
    if fatal:
        _log.warning("validation found %d fatal issues; continuing with valid clips", len(fatal))

    # 1. Download
    _cb("downloading", 0)
    clips = download_all(project.sorted_by_rank(), cfg)
    ok_clips = [c for c in clips if c.status != ClipStatus.FAILED]
    _log.info("downloaded %d/%d clips", len(ok_clips), total)

    # 2. Analyze
    _cb("analyzing", 0)
    clips = analyze_all(ok_clips, cfg)
    ok_clips = [c for c in clips if c.status != ClipStatus.FAILED]

    # 3. Normalize
    _cb("normalizing", 0)
    clips = normalize_all(ok_clips, cfg)
    ok_clips = [c for c in clips if c.status == ClipStatus.NORMALIZED and c.normalized_path]
    _log.info("normalized %d/%d clips", len(ok_clips), total)

    if not ok_clips:
        raise RuntimeError("no valid clips to render after pipeline")

    # 4. Overlays
    _cb("overlays", 0)
    overlay_paths = render_overlays(ok_clips, project.project_title, cfg)

    # 5. Final compose
    _cb("composing", 0)
    clip_paths = [Path(c.normalized_path) for c in ok_clips]
    cmd = build_overlay_command(clip_paths, overlay_paths, output_path, cfg)
    proc = run_ffmpeg(cmd, cfg)
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"final compose failed (rc={proc.returncode})")

    _cb("done", total)
    _log.info("final video: %s", output_path)

    # 6. Segment analysis report
    plan = build_render_plan(project, cfg, output_path=output_path)
    plan.clip_reports = [c.to_report_row() for c in ok_clips]
    _print_report(plan)
    return output_path


def _load_project_arg(args: argparse.Namespace, cfg: AppConfig) -> ProjectDefinition:
    """Load a project from --config (JSON) or --csv with --title."""
    if args.config:
        return load_project_json(Path(args.config))
    if args.csv:
        return load_project_csv(Path(args.csv), title=args.title or "Ranking Video")
    raise SystemExit("Provide --config (JSON) or --csv (CSV file).")


def _environment_check(cfg: AppConfig) -> int:
    """Verify all external tools are reachable; print a human-readable report."""
    from .ffmpeg_utils import ffmpeg_bin, ffprobe_bin

    print("Environment check")
    print("-" * 40)
    ff = ffmpeg_bin(cfg)
    fp = ffprobe_bin(cfg)
    print(f"ffmpeg : {ff}")
    print(f"ffprobe: {fp}")

    import shutil
    import subprocess

    ok = True
    for name, path in (("ffmpeg", ff), ("ffprobe", fp)):
        try:
            r = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
            status = "OK" if r.returncode == 0 else f"FAIL rc={r.returncode}"
            if r.returncode != 0:
                ok = False
        except Exception as exc:
            status = f"MISSING ({exc})"
            ok = False
        print(f"  {name:8s}: {status}")
    try:
        import yt_dlp  # noqa: F401
        print(f"  yt-dlp  : OK ({yt_dlp.version.__version__})")
    except ImportError:
        print("  yt-dlp  : NOT INSTALLED  (pip install yt-dlp)")
        ok = False
    try:
        import cv2  # noqa: F401
        print("  opencv  : OK")
    except ImportError:
        print("  opencv  : NOT INSTALLED (motion detection disabled)")
    try:
        import PIL  # noqa: F401
        print("  pillow  : OK")
    except ImportError:
        print("  pillow  : NOT INSTALLED (overlay rendering needs it)")

    print("-" * 40)
    print("Result:", "ALL GOOD" if ok else "ISSUES FOUND — see above")
    return 0 if ok else 1


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point: ``python -m src.renderer``."""
    parser = argparse.ArgumentParser(description="Ranking Videos Generator")
    parser.add_argument("--config", help="Path to project JSON file")
    parser.add_argument("--csv", help="Path to project CSV file")
    parser.add_argument("--title", help="Project title (used with --csv)")
    parser.add_argument("--output", "-o", help="Output MP4 path")
    parser.add_argument("--dry-run", action="store_true", help="Validate + print plan only")
    parser.add_argument("--check", action="store_true", help="Verify ffmpeg/ffprobe/yt-dlp then exit")
    parser.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")
    args = parser.parse_args(argv)

    cfg = AppConfig.from_env()
    configure_logging(level=args.log_level or cfg.log_level)

    if args.check:
        return _environment_check(cfg)

    if args.dry_run:
        dry_run(project, cfg)
        return 0

    out = render_project(project, cfg, output_path=Path(args.output) if args.output else None)
    print(f"\nRender complete: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())