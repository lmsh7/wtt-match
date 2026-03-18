"""Parse OCR text into structured match data with multi-frame voting."""

from __future__ import annotations

import logging
import re
from collections import Counter

from .models import FrameResult, MatchInfo

logger = logging.getLogger(__name__)

# Words that indicate a scoreboard header, NOT a player name
_HEADER_KEYWORDS = {
    "SINGLES", "DOUBLES", "MIXED", "GIRLS", "BOYS", "MEN", "WOMEN",
    "ROUND", "FINAL", "SEMI", "QUARTER", "GROUP", "MATCH", "GAME",
    "POINT", "MATCHPOINT", "GAMEPOINT", "SETPOINT",
    "SESSION", "TABLE", "LIVE", "WTT", "CONTENDER", "STAR",
    "HAVIROV",  # venue name in this video
}

# Country flag indicators (OCR sometimes picks up flag emoji or code)
_COUNTRY_CODES = {
    "CHN", "JPN", "KOR", "GER", "SWE", "BRA", "FRA", "IND", "TPE",
    "HKG", "SGP", "THA", "EGY", "NGA", "POR", "AUT", "CRO", "POL",
    "ROU", "CZE", "SLO", "HUN", "DEN", "USA", "AUS", "ESP", "ITA",
    "ENG", "TUR", "LUX", "PRK", "MAS", "QAT", "UAE", "SVK", "NOR",
    "FIN", "BEL", "NED", "SUI", "UKR", "RUS", "ISR", "MEX", "ARG",
    "COL", "CAN", "NZL", "RSA",
}

# Pattern: all-caps text that looks like a player name (at least 4 chars)
# Matches "YAO RUIXUAN", "SZYMANSKA HANNA", "BARTOVAADELA"
_ALLCAPS_NAME = re.compile(r"^[A-Z][A-Z\s]{3,}$")

# Score pattern
_SCORE_PATTERN = re.compile(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b")


def _clean_text(text: str) -> str:
    """Clean OCR artifacts from text."""
    # Remove common OCR noise characters
    text = re.sub(r"[|!'\"\[\]{}()<>:;,.]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_player_name(text: str) -> bool:
    """Check if a text string looks like a player name."""
    cleaned = _clean_text(text).upper()

    # Must be mostly uppercase letters
    if not _ALLCAPS_NAME.match(cleaned):
        return False

    # Must not be a header keyword
    words = cleaned.split()
    if any(w in _HEADER_KEYWORDS for w in words):
        return False

    # Must not be only a country code
    if cleaned in _COUNTRY_CODES:
        return False

    # Must not be too short (single word < 4 chars) or just digits
    if len(cleaned) < 4:
        return False

    # Should contain at least some vowels (real names have vowels)
    vowels = set("AEIOU")
    if not any(c in vowels for c in cleaned):
        return False

    # Filter out common OCR garbage patterns (R32, R16, etc.)
    if re.match(r"^R\d+", cleaned):
        return False

    return True


def _normalize_name(text: str) -> str:
    """Normalize a player name for consistent matching."""
    cleaned = _clean_text(text).upper()
    # Remove any leading/trailing single chars (OCR artifacts like flag boxes)
    parts = cleaned.split()
    parts = [p for p in parts if len(p) > 1 or p in ("I", "O")]
    return " ".join(parts)


def parse_frame_ocr(
    timestamp: float,
    ocr_results: list[tuple[str, float]],
) -> FrameResult:
    """Extract player names and scores from OCR results of a single frame.

    Strategy: treat each OCR text block as a potential player name line,
    since the scoreboard has names on separate lines.
    """
    result = FrameResult(timestamp=timestamp)
    result.raw_texts = [text for text, _ in ocr_results]

    if not ocr_results:
        return result

    avg_conf = sum(c for _, c in ocr_results) / len(ocr_results)
    result.confidence = avg_conf

    # Check each OCR text block independently as a potential player name
    player_names: list[str] = []
    for text, conf in ocr_results:
        if _is_player_name(text):
            name = _normalize_name(text)
            if name and name not in player_names:
                player_names.append(name)

    if len(player_names) >= 2:
        result.player1 = player_names[0]
        result.player2 = player_names[1]
    elif len(player_names) == 1:
        result.player1 = player_names[0]

    # Extract scores
    all_text = " ".join(text for text, _ in ocr_results)
    scores = _SCORE_PATTERN.findall(all_text)
    if scores:
        result.score1 = scores[0][0]
        result.score2 = scores[0][1]

    return result


def aggregate_matches(
    frame_results: list[FrameResult],
    gap_threshold: float | None = None,
    interval: float = 5.0,
) -> list[MatchInfo]:
    """Aggregate frame results into matches using multi-frame voting.

    Frames with the same player pair within *gap_threshold* seconds
    are considered part of the same match. If gap_threshold is None,
    it defaults to interval * 3 (adaptive to sampling rate).
    """
    if gap_threshold is None:
        gap_threshold = interval * 3
    if not frame_results:
        return []

    # Filter frames that have at least one player identified
    valid_frames = [f for f in frame_results if f.player1]
    if not valid_frames:
        return []

    # Group consecutive frames into match segments
    segments: list[list[FrameResult]] = []
    current_segment: list[FrameResult] = [valid_frames[0]]

    for frame in valid_frames[1:]:
        prev = current_segment[-1]
        # Same match if close in time and similar players
        time_gap = frame.timestamp - prev.timestamp
        same_players = _similar_players(frame, prev)

        if time_gap <= gap_threshold and same_players:
            current_segment.append(frame)
        else:
            segments.append(current_segment)
            current_segment = [frame]

    segments.append(current_segment)

    # Vote on player names for each segment
    matches = []
    for i, segment in enumerate(segments, 1):
        if len(segment) < 1:
            continue

        p1_counter: Counter[str] = Counter()
        p2_counter: Counter[str] = Counter()
        confidences = []

        for frame in segment:
            if frame.player1:
                p1_counter[frame.player1] += 1
            if frame.player2:
                p2_counter[frame.player2] += 1
            confidences.append(frame.confidence)

        if not p1_counter:
            continue

        player1 = p1_counter.most_common(1)[0][0]
        player2 = p2_counter.most_common(1)[0][0] if p2_counter else "Unknown"
        avg_conf = sum(confidences) / len(confidences)

        match = MatchInfo(
            match_number=i,
            player1=player1,
            player2=player2,
            start_time=segment[0].timestamp,
            end_time=segment[-1].timestamp,
            confidence=avg_conf,
            frame_count=len(segment),
        )
        matches.append(match)
        logger.info(
            "Match %d: %s vs %s (%s - %s, %d frames, conf=%.2f)",
            match.match_number,
            match.player1,
            match.player2,
            match.start_time_fmt,
            match.end_time_fmt,
            match.frame_count,
            match.confidence,
        )

    # Re-number matches sequentially
    for i, match in enumerate(matches, 1):
        match.match_number = i

    return matches


def _similar_players(a: FrameResult, b: FrameResult) -> bool:
    """Check if two frames likely show the same match."""
    if not a.player1 or not b.player1:
        return True  # can't tell, assume same

    a_players = {a.player1, a.player2} - {None}
    b_players = {b.player1, b.player2} - {None}

    # Exact match
    if a_players & b_players:
        return True

    # Fuzzy: check if any name from a is a substring of any name from b, or vice versa
    # This handles OCR noise like "IYAO RUIXUAN" vs "YAORUIXUAN"
    for pa in a_players:
        for pb in b_players:
            if _fuzzy_name_match(pa, pb):
                return True

    return False


def _fuzzy_name_match(a: str, b: str) -> bool:
    """Check if two OCR'd names likely refer to the same player.

    Handles OCR noise: prefix/suffix junk chars, missing spaces, etc.
    Requires at least 60% overlap to avoid false merges.
    """
    a_compact = a.replace(" ", "")
    b_compact = b.replace(" ", "")

    shorter = min(a_compact, b_compact, key=len)
    longer = max(a_compact, b_compact, key=len)

    if len(shorter) < 4:
        return False

    # One contains the other, and the shorter is at least 60% of the longer
    # This handles "IYAO RUIXUAN" contains "YAORUIXUAN" (or vice versa)
    if shorter in longer and len(shorter) / len(longer) >= 0.6:
        return True

    return False
