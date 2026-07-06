# Ranking Videos Generator

Production-grade Python application that generates vertical **9:16 Ranking / Top List Shorts**
for TikTok, YouTube Shorts, and Instagram Reels.

It ingests short clips from internet URLs, ranks them via JSON/CSV config, optionally detects
the funniest / most shocking moment inside each clip, then renders one polished vertical video
with overlay graphics (title, ranking sidebar, captions, highlights, transitions).

## Features

- Import clips from internet URLs (direct HTTP or yt-dlp supported sites)
- JSON **and** CSV project definitions
- Manual **and** semi-automatic best-moment detection
- Heuristic scoring pipeline (audio loudness, motion, brightness/scene energy) + pluggable AI hooks
- 1080x1920 vertical output with smart-fit source scaling (crop or pad)
- FFmpeg filter_complex overlay pipeline (drawtext, ranking sidebar, caption, highlight, transitions)
- Loudness normalization, hardware-acceleration hooks with software fallback
- Streamlit UI with editable clip table, validation, dry-run, render progress
- Dry-run mode + segment analysis report (timestamps + confidence)
- Graceful failure: invalid clips are skipped, valid ones still render

## Project structure

```
ranking-videos-generator/
  app.py
  requirements.txt
  .env.example
  config/
    sample_project.json
  data/
    sample_ranking.csv
  assets/
    fonts/    sfx/    overlays/
  src/
    __init__.py
    config_schema.py
    models.py
    downloader.py
    normalizer.py
    detector.py
    ranking.py
    compositor.py
    renderer.py
    ui_helpers.py
    ffmpeg_utils.py
    logging_utils.py
  output/
  temp/
  tests/
```

## Setup (Windows)

1. Install **Python 3.11+** (`https://python.org`).
2. Install **FFmpeg** (builds from `https://www.gyan.dev/ffmpeg/builds/`). Ensure `ffmpeg.exe`
   and `ffprobe.exe` are on `PATH`, or set `FFMPEG_BIN` / `FFPROBE_BIN` in `.env`.
3. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and edit if needed.

## Usage

### CLI (dry run + render)

```powershell
# Validate config and print the render plan (no download / no render)
python -m src.renderer --config config/sample_project.json --dry-run

# Full render
python -m src.renderer --config config/sample_project.json --output output/final.mp4

# Render from CSV
python -m src.renderer --csv data/sample_ranking.csv --title "Ranking Pool Fails" --dry-run
```

### Streamlit UI

```powershell
streamlit run app.py
```

Open the sidebar to set the project title, output resolution, detection mode, transitions,
paste URLs or upload a CSV/JSON, edit captions and order, then Validate / Analyze / Render.

## Input formats

**JSON** (`config/sample_project.json`):
```json
{
  "project_title": "Ranking Best Pool Fails",
  "output_resolution": "1080x1920",
  "clips": [
    {"rank": 6, "url": "https://example.com/v1.mp4", "caption": "Aaah", "start_time": null, "end_time": null, "detection_mode": "auto"},
    {"rank": 5, "url": "https://example.com/v2.mp4", "caption": "So close", "start_time": 2.1, "end_time": 5.8, "detection_mode": "manual"}
  ]
}
```

**CSV** (`data/sample_ranking.csv`) columns:
```
rank,url,caption,start_time,end_time,detection_mode
```

## Detection modes

- `auto`    — heuristic pipeline (audio spike, motion, scene energy) picks the best segment.
- `manual`  — use `start_time` / `end_time` from config.
- `center`  — use the center segment of the clip (fallback).

Confidence scores (0.0–1.0) are reported for each detected segment.

## Notes

- All configurable values live in `src/config_schema.py` (centralized schema).
- FFmpeg command generation is encapsulated in `src/ffmpeg_utils.py`.
- Modules are independently importable and unit-testable.
- Generated media is cached under `temp/` to avoid re-downloads.