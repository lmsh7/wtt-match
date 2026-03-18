"""CLI entry point for wtt-match."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

from .models import FrameResult
from .ocr import get_engine, run_ocr
from .parser import parse_frame_ocr
from .preprocessor import preprocess_frame, preprocess_frame_prioritized
from .streamer import check_dependencies, extract_frames_parallel

logger = logging.getLogger(__name__)


def _process_single_frame(
    timestamp: float,
    frame: "np.ndarray",
    debug: bool,
    debug_dir: Path | None,
) -> FrameResult:
    """Process a single frame: preprocess -> OCR -> pick best result.

    Uses priority-ordered variants with early-exit: stops as soon as
    both players are detected (the common case for WTT scoreboards).
    Falls back to exhaustive search only when needed.
    """
    if debug and debug_dir:
        cv2.imwrite(
            str(debug_dir / f"frame_{timestamp:08.1f}_raw.png"),
            frame,
        )

    best_result: FrameResult | None = None
    best_score: tuple[int, float] = (0, 0.0)

    for roi, variant, processed in preprocess_frame_prioritized(frame):
        if debug and debug_dir:
            cv2.imwrite(
                str(debug_dir / f"frame_{timestamp:08.1f}_{roi.name}_{variant}.png"),
                processed,
            )

        ocr_results = run_ocr(processed)
        if not ocr_results:
            continue

        parsed = parse_frame_ocr(timestamp, ocr_results)

        has_both = parsed.player1 is not None and parsed.player2 is not None
        has_one = parsed.player1 is not None
        score = (2 if has_both else 1 if has_one else 0, parsed.confidence)

        if score > best_score:
            best_result = parsed
            best_score = score

        # Early exit: both players found with decent confidence — no need
        # to try remaining variants or ROIs.
        if best_score[0] == 2:
            break

    if best_result is None:
        best_result = FrameResult(timestamp=timestamp)

    return best_result


def process_video(
    video_url: str,
    interval: float = 600.0,
    debug: bool = False,
    output_dir: Path | None = None,
    max_workers: int = 4,
) -> dict:
    """Main processing pipeline: extract frames -> preprocess -> OCR -> aggregate."""
    import numpy as np

    check_dependencies()

    if output_dir is None:
        output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    debug_dir = output_dir / "debug"
    if debug:
        debug_dir.mkdir(parents=True, exist_ok=True)

    # Pre-warm OCR engine so the user sees progress clearly
    print("[1/3] Loading OCR engine...", end=" ", flush=True)
    t0 = time.time()
    get_engine()
    print(f"done ({time.time() - t0:.1f}s)")

    print(f"[2/3] Fetching frames (parallel, workers={max_workers})...", end=" ", flush=True)
    t0 = time.time()
    frames = extract_frames_parallel(video_url, interval=interval, max_workers=max_workers)
    total_frames = len(frames)
    print(f"done ({time.time() - t0:.1f}s, {total_frames} frames)")

    print(f"[3/3] Processing frames (parallel, workers={max_workers})...")
    t0 = time.time()

    _fmt = lambda s: f"{int(s)//3600:02d}:{(int(s)%3600)//60:02d}:{int(s)%60:02d}"
    frame_results: list[FrameResult] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_single_frame, timestamp, frame, debug, debug_dir if debug else None
            ): timestamp
            for timestamp, frame in frames
        }
        for future in as_completed(futures):
            ts = futures[future]
            result = future.result()
            frame_results.append(result)
            completed += 1

            players = ""
            if result.player1:
                players = f" -> {result.player1} vs {result.player2 or '?'}"
            pct = completed * 100 // (total_frames or 1)
            print(f"  [{completed}/{total_frames}] t={_fmt(ts)} ({pct}%){players}")

    # Sort by timestamp to maintain deterministic output
    frame_results.sort(key=lambda r: r.timestamp)

    print(f"Done. {total_frames} frames processed in {time.time() - t0:.1f}s.")

    # Build per-frame results (no aggregation)
    frames_output = []
    for fr in frame_results:
        if fr.player1:
            frames_output.append({
                "timestamp": fr.timestamp,
                "time_fmt": _fmt(fr.timestamp),
                "player1": fr.player1,
                "player2": fr.player2,
                "confidence": round(fr.confidence, 2),
            })

    result = {
        "video_url": video_url,
        "total_frames": total_frames,
        "frames": frames_output,
    }

    # Write JSON output
    output_file = output_dir / "matches.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info("Results written to %s", output_file)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Identify table tennis players from WTT YouTube videos via OCR",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="YouTube video URL",
    )
    parser.add_argument(
        "--player",
        type=str,
        default=None,
        help="Filter results to matches containing this player name (case-insensitive fuzzy match)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=600.0,
        help="Frame sampling interval in seconds (default: 600, i.e. 10 min)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save debug frames to output/debug/",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers for frame grabbing and OCR (default: 4)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = process_video(
        video_url=args.url,
        interval=args.interval,
        debug=args.debug,
        output_dir=Path(args.output),
        max_workers=args.workers,
    )

    frames = result["frames"]

    # Filter by player name if specified
    if args.player:
        query = args.player.upper()
        frames = [
            f for f in frames
            if query in f["player1"].upper()
            or (f["player2"] and query in f["player2"].upper())
        ]

    # Print summary
    if args.player:
        print(f"\nFrames matching '{args.player}':")
    else:
        print(f"\nDetected {len(frames)} frame(s) with players:")

    for f in frames:
        p2 = f["player2"] or "?"
        print(f"  {f['time_fmt']}  {f['player1']} vs {p2}  (conf={f['confidence']:.2f})")

    if not frames:
        print("  (none)")
        sys.exit(0)


if __name__ == "__main__":
    main()
