"""Overlay asset generation.

For each ranked clip the compositor renders a transparent 1080x1920 PNG that
contains:
  - the big bold **title** at the top
  - the **ranking sidebar** on the left with all rank numbers stacked vertically
  - the **highlighted** rank for the *current* clip (different colour + glow)
  - the clip-specific **caption** near the active area
  - safe-zone margins respected

The PNGs are then composited over each clip by the FFmpeg overlay pipeline in
:mod:`src.ffmpeg_utils`. Pillow is used because drawtext on long UTF-8 captions
and emoji on Windows ffmpeg can be flaky; a rendered PNG is reliable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config_schema import AppConfig, CanvasConfig
from .logging_utils import get_logger
from .models import ProcessedClip, ClipStatus

_log = get_logger("compositor")

_FALLBACK_FONT_NAMES = (
    "arialbd.ttf", "arialbd", "Arial-Bold.ttf", "ArialBold.ttf",
    "segoeuib.ttf", "DejaVuSans-Bold.ttf", "arial.ttf",
)


def _to_pil_color(color: str) -> str:
    """Convert config color formats (``0xRRGGBB`` or hex) to Pillow's ``#RRGGBB``."""
    c = (color or "white").strip()
    if c.startswith("0x") or c.startswith("0X"):
        return "#" + c[2:]
    if c.startswith("#"):
        return c
    return c


def _load_font(size: int, cfg: AppConfig) -> ImageFont.FreeTypeFont:
    """Load a bold font, trying explicit asset dir then OS fallbacks."""
    candidates: list[Path] = []
    explicit = cfg.paths.fonts_dir
    if explicit.exists():
        candidates += sorted(explicit.glob("*.ttf")) + sorted(explicit.glob("*.otf"))
    fonts_dir = Path("C:/Windows/Fonts")
    for name in _FALLBACK_FONT_NAMES:
        candidates.append(fonts_dir / name)
    # try PIL default location discovery
    for c in candidates:
        try:
            return ImageFont.truetype(str(c), size=size)
        except OSError:
            continue
    _log.warning("no TrueType font found; using PIL default (text may look plain)")
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,
    fill: str,
    stroke: str = "black",
    stroke_width: int = 6,
    anchor: str = "lt",
) -> None:
    draw.text(
        xy, text, font=font, fill=fill,
        stroke_width=stroke_width, stroke_fill=stroke, anchor=anchor,
    )


def render_overlay_png(
    clip: ProcessedClip,
    all_ranks: list[int],
    canvas: CanvasConfig,
    cfg: AppConfig,
    out_path: Path,
) -> Path:
    """Render a transparent overlay PNG for a single clip.

    Args:
        clip: the active clip (its rank is highlighted).
        all_ranks: every rank shown in the sidebar.
        canvas: geometry/colour config.
        cfg: app config (used for font/dir resolution).
        out_path: destination PNG path.
    """
    W, H = canvas.width, canvas.height
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    title_font = _load_font(canvas.title_font_size, cfg)
    rank_font = _load_font(canvas.rank_font_size, cfg)
    caption_font = _load_font(canvas.caption_font_size, cfg)

    # The project title is drawn separately by ``add_title_to_overlay`` so it is
    # applied uniformly across all per-clip images.

    # --- Ranking sidebar (left) ---
    # For the ACTIVE clip we draw "#rank  caption" as ONE single text line in
    # one size, so the rank and its caption appear together at the start of the
    # related clip and stay for the whole clip. Non-active ranks show only the
    # rank number (smaller, dim) so the active line stands out.
    sidebar_w = canvas.sidebar_width
    sidebar_x = 0
    n = len(all_ranks)
    start_y = canvas.safe_margin_top + canvas.title_height + 20
    slot_h = canvas.rank_font_size + 32
    total_h = n * slot_h
    if start_y + total_h > H - canvas.safe_margin_bottom:
        slot_h = max(56, (H - canvas.safe_margin_bottom - start_y) // max(n, 1))

    # we reuse rank_font (== caption_font_size now) for the active merged line
    merged_font = rank_font
    inactive_font = _load_font(max(36, canvas.rank_font_size - 18), cfg)

    for i, r in enumerate(all_ranks):
        y = start_y + i * slot_h
        is_active = r == clip.spec.rank
        if is_active:
            color = _to_pil_color(canvas.active_rank_color)
            # glow behind the active row
            glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(glow)
            gdraw.rectangle(
                [sidebar_x, y, sidebar_w - 20, y + slot_h - 6],
                fill=(255, 210, 63, 70),
            )
            glow = glow.filter(ImageFilter.GaussianBlur(14))
            img.alpha_composite(glow)
            # solid accent bar
            draw.rectangle(
                [sidebar_x, y, sidebar_x + 14, y + slot_h - 6],
                fill=_to_pil_color(canvas.active_rank_color),
            )
            # merged single line: "#rank  caption"
            caption = (clip.spec.caption or "").strip()
            line = f"#{r}  {caption}" if caption else f"#{r}"
            _draw_outlined_text(
                draw, (sidebar_x + 40, y + slot_h // 2 - canvas.rank_font_size // 2),
                line, merged_font, fill=color,
                stroke=_to_pil_color(canvas.stroke_color),
                stroke_width=canvas.stroke_width, anchor="lm",
            )
        else:
            color = _to_pil_color(canvas.rank_color)
            _draw_outlined_text(
                draw, (sidebar_x + 40, y + slot_h // 2 - 36),
                f"#{r}", inactive_font, fill=color,
                stroke=_to_pil_color(canvas.stroke_color),
                stroke_width=canvas.stroke_width, anchor="lm",
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    _log.debug("overlay written -> %s", out_path)
    return out_path


def add_title_to_overlay(
    overlay_path: Path,
    title: str,
    canvas: CanvasConfig,
    cfg: AppConfig,
) -> None:
    """Draw the project title at the top of an existing overlay PNG (in place).

    No background box — only a soft drop shadow for readability, with left and
    right margins so the title never touches the screen edges.
    """
    img = Image.open(overlay_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    font = _load_font(canvas.title_font_size, cfg)
    margin_x = canvas.title_margin_x
    max_w = img.width - 2 * margin_x
    # shrink-wrapped fit: if the title is wider than the available area, reduce
    # font size until it fits (keeps it on one line and clear on mobile).
    size = canvas.title_font_size
    while size > 24:
        font = _load_font(size, cfg)
        tw = _text_size(draw, title, font)[0]
        if tw <= max_w:
            break
        size -= 4
    tw = _text_size(draw, title, font)[0]
    x = (img.width - tw) // 2
    y = canvas.safe_margin_top
    # drop shadow only (no filled box behind the title)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.text((x + 5, y + 5), title, font=font, fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    img.alpha_composite(shadow)
    _draw_outlined_text(
        draw, (x, y), title, font,
        fill=_to_pil_color(canvas.title_color), stroke=_to_pil_color(canvas.stroke_color),
        stroke_width=canvas.stroke_width + 1, anchor="lt",
    )
    img.save(overlay_path)


def render_overlays(
    clips: list[ProcessedClip],
    project_title: str,
    cfg: AppConfig,
) -> list[Path]:
    """Render one overlay PNG per clip (only for NORMALIZED/valid clips).

    Returns the list of overlay PNG paths aligned 1:1 with ``clips`` (None-ish
    for failed clips but callers should filter first)."""
    canvas = cfg.canvas
    valid = [c for c in clips if c.status not in (ClipStatus.FAILED, ClipStatus.SKIPPED)]
    all_ranks = [c.spec.rank for c in valid]
    images: list[Path] = []
    for c in valid:
        out = cfg.paths.overlays_dir / f"overlay_rank{c.spec.rank}.png"
        render_overlay_png(c, all_ranks, canvas, cfg, out)
        add_title_to_overlay(out, project_title, canvas, cfg)
        images.append(out)
    return images