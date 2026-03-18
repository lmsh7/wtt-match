from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FrameResult:
    """OCR result from a single frame."""

    timestamp: float
    player1: str | None = None
    player2: str | None = None
    score1: str | None = None
    score2: str | None = None
    raw_texts: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class MatchInfo:
    """Aggregated match information from multiple frames."""

    match_number: int
    player1: str
    player2: str
    start_time: float
    end_time: float
    confidence: float
    frame_count: int

    @property
    def start_time_fmt(self) -> str:
        return _fmt_time(self.start_time)

    @property
    def end_time_fmt(self) -> str:
        return _fmt_time(self.end_time)

    def to_dict(self) -> dict:
        return {
            "match_number": self.match_number,
            "player1": self.player1,
            "player2": self.player2,
            "start_time_fmt": self.start_time_fmt,
            "end_time_fmt": self.end_time_fmt,
            "confidence": round(self.confidence, 2),
            "frame_count": self.frame_count,
        }


def _fmt_time(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h:02d}:{m:02d}:{s:02d}"
