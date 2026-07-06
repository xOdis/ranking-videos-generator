"""Tests for ffmpeg_utils command builders (pure functions, no execution)."""
from __future__ import annotations

from pathlib import Path

from src.config_schema import AppConfig
from src.ffmpeg_utils import (
    build_normalize_command,
    build_overlay_command,
    escape_drawtext,
    is_url,
    hwaccel_input_args,
)


def test_is_url():
    assert is_url("https://x.com/v.mp4")
    assert not is_url("ftp://x.com")
    assert not is_url("")


def test_escape_drawtext():
    out = escape_drawtext("A:b%c\\d'e")
    # colon and percent escaped; backslash doubled
    assert "\\:" in out and "\\%" in out and "\\\\" in out


def test_hwaccel_args_default_none():
    cfg = AppConfig.from_env()
    assert hwaccel_input_args(cfg) == []


def test_build_normalize_command_crop():
    cfg = AppConfig.from_env()
    cmd = build_normalize_command(
        Path("in.mp4"), Path("out.mp4"), cfg, start=1.0, end=4.0
    )
    assert cmd[cmd.index("-i") + 1] == "in.mp4"
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=-2:1920" in vf  # crop scales by height
    assert "crop=1080:1920" in vf


def test_build_overlay_command_structure():
    cfg = AppConfig.from_env()
    clips = [Path("a.mp4"), Path("b.mp4")]
    overlays = [Path("oa.png"), Path("ob.png")]
    cmd = build_overlay_command(clips, overlays, Path("out.mp4"), cfg)
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "concat=n=2" in fc
    assert "loudnorm" in fc
    assert "[vfx]" not in fc


def test_build_overlay_command_mismatch():
    import pytest
    cfg = AppConfig.from_env()
    with pytest.raises(ValueError):
        build_overlay_command([Path("a.mp4")], [Path("x.png"), Path("y.png")], Path("o.mp4"), cfg)