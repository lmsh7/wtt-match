"""yt-dlp stream URL resolution + ffmpeg remote frame grabbing (no disk I/O)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

logger = logging.getLogger(__name__)


def check_dependencies() -> None:
    """Check that ffmpeg and yt-dlp are available on PATH."""
    for cmd in ("ffmpeg", "yt-dlp"):
        if shutil.which(cmd) is None:
            raise RuntimeError(
                f"'{cmd}' not found on PATH. "
                f"Install it with: brew install {cmd}"
            )


_video_info_cache: dict[str, dict] = {}


def get_video_info(video_url: str) -> dict:
    """Get video metadata via yt-dlp --dump-json (cached)."""
    if video_url in _video_info_cache:
        return _video_info_cache[video_url]
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]/best",
        "--dump-json",
        "--no-warnings",
        video_url,
    ]
    logger.info("Getting video info: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed to get video info:\n{proc.stderr.strip()}"
        )
    info = json.loads(proc.stdout)
    _video_info_cache[video_url] = info
    return info


def get_stream_url(video_url: str) -> str:
    """Get the direct stream URL for a video (360p or best available <=360p)."""
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080]/best",
        "--get-url",
        "--no-warnings",
        video_url,
    ]
    logger.info("Getting stream URL: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed to get stream URL:\n{proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def get_duration_and_resolution(video_url: str) -> tuple[float, int, int]:
    """Return (duration_seconds, width, height) for the video."""
    info = get_video_info(video_url)
    duration = float(info.get("duration", 0))
    width = int(info.get("width", 640))
    height = int(info.get("height", 360))
    logger.info("Video: %.0fs, %dx%d", duration, width, height)
    return duration, width, height


def grab_frame(
    stream_url: str,
    timestamp: float,
    width: int,
    height: int,
) -> np.ndarray | None:
    """Grab a single frame at *timestamp* from the remote stream via ffmpeg pipe.

    Returns an BGR numpy array (height, width, 3) or None on failure.
    """
    cmd = [
        "ffmpeg",
        "-ss", f"{timestamp:.2f}",  # input seeking (fast, uses HTTP range)
        "-i", stream_url,
        "-frames:v", "1",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-v", "error",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timed out at t=%.1f", timestamp)
        return None

    expected_size = width * height * 3
    if len(proc.stdout) != expected_size:
        stderr_msg = proc.stderr.decode(errors="replace")[:200] if proc.stderr else ""
        logger.warning(
            "Frame size mismatch at t=%.1f: got %d, expected %d. stderr: %s",
            timestamp,
            len(proc.stdout),
            expected_size,
            stderr_msg,
        )
        return None

    frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(height, width, 3)
    return frame


def extract_frames(
    video_url: str,
    interval: float = 600.0,
) -> Generator[tuple[float, np.ndarray], None, None]:
    """Yield (timestamp, frame) tuples, one frame every *interval* seconds."""
    check_dependencies()
    info = get_video_info(video_url)
    duration = float(info.get("duration", 0))
    width = int(info.get("width", 640))
    height = int(info.get("height", 360))
    # Always use --get-url for the stream URL (more reliable for ffmpeg)
    stream_url = get_stream_url(video_url)
    logger.info("Video: %.0fs, %dx%d", duration, width, height)

    total_frames = int(duration / interval)
    logger.info(
        "Will extract ~%d frames (every %.0fs over %.0fs)",
        total_frames,
        interval,
        duration,
    )

    t = 0.0
    extracted = 0
    while t < duration:
        frame = grab_frame(stream_url, t, width, height)
        if frame is not None:
            extracted += 1
            if extracted % 100 == 0:
                logger.info("Extracted %d/%d frames", extracted, total_frames)
            yield (t, frame)
        t += interval


def extract_frames_parallel(
    video_url: str,
    interval: float = 600.0,
    max_workers: int = 4,
) -> list[tuple[float, np.ndarray]]:
    """Extract frames in parallel using a thread pool.

    Returns a list of (timestamp, frame) tuples sorted by timestamp.
    """
    check_dependencies()
    info = get_video_info(video_url)
    duration = float(info.get("duration", 0))
    width = int(info.get("width", 640))
    height = int(info.get("height", 360))
    stream_url = get_stream_url(video_url)
    logger.info("Video: %.0fs, %dx%d", duration, width, height)

    timestamps = []
    t = 0.0
    while t < duration:
        timestamps.append(t)
        t += interval

    logger.info(
        "Will extract %d frames with %d workers",
        len(timestamps),
        max_workers,
    )

    results: list[tuple[float, np.ndarray]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(grab_frame, stream_url, ts, width, height): ts
            for ts in timestamps
        }
        for future in as_completed(futures):
            ts = futures[future]
            frame = future.result()
            if frame is not None:
                results.append((ts, frame))

    results.sort(key=lambda x: x[0])
    return results
