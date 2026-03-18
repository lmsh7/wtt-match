# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WTT Match is a CLI tool that identifies table tennis players from WTT (World Table Tennis) YouTube videos using OCR. It extracts frames from videos at regular intervals, preprocesses scoreboard regions, runs OCR, and parses player names and scores.

## Commands

```bash
# Install dependencies (uses uv with Python 3.12 venv)
uv sync

# Run the tool
uv run wtt-match --url <YOUTUBE_URL> [--interval 600] [--debug] [--output output] [--workers 4] [-v]

# Run with debug frames saved
uv run wtt-match --url <URL> --debug

# Filter results by player name
uv run wtt-match --url <URL> --player "ZHANG"
```

## External Dependencies

Requires `ffmpeg` and `yt-dlp` on PATH (`brew install ffmpeg yt-dlp`).

## Architecture

The pipeline follows a linear flow: **streamer -> preprocessor -> ocr -> parser**

- **streamer.py** — Resolves YouTube URLs via yt-dlp, grabs individual frames via ffmpeg pipe (no disk I/O for video). Supports parallel frame extraction with ThreadPoolExecutor.
- **preprocessor.py** — Crops scoreboard ROIs (bottom-left primary, top-left fallback), applies CLAHE + Gaussian blur + sharpening, then produces 4 binarization variants (otsu_inv, adaptive, otsu, gray). Priority-ordered generator enables early-exit when both players are found.
- **ocr.py** — Thin wrapper around RapidOCR (ONNX runtime). Singleton engine with tuned params (`det_limit_side_len=320`, `use_cls=False`) for scoreboard-sized text.
- **parser.py** — Extracts player names from OCR text using regex + heuristic filters (header keywords, country codes, vowel check). Includes fuzzy name matching and multi-frame voting for match aggregation.
- **main.py** — CLI entry point and orchestrator. Parallel processing of frames with best-result selection (scored by player count + confidence). Outputs `matches.json`.
- **models.py** — `FrameResult` (per-frame) and `MatchInfo` (aggregated) dataclasses.

### Key Design Decisions

- Frames are grabbed directly from remote streams via ffmpeg pipe — no temp files on disk.
- Scoreboard ROIs use fractional coordinates (0-1) to work at any resolution.
- Multiple binarization variants are tried per ROI in priority order; processing stops early once both players are detected.
- WTT scoreboards use light text on dark backgrounds, so `otsu_inv` is the highest-priority variant.
