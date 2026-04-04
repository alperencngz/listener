"""Meeting analytics — compute metrics from diarized transcript segments.

Provides talk-time distribution, turn counts, silence analysis,
and other meeting health metrics for the Analytics Dashboard (F8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from listener.transcriber import Segment


@dataclass
class SpeakerStats:
    """Analytics for a single speaker."""
    talk_time: float = 0.0        # total seconds spoken
    turn_count: int = 0           # number of speaking turns
    avg_turn_length: float = 0.0  # average seconds per turn
    percentage: float = 0.0       # percentage of total talk time

    def to_dict(self) -> dict:
        return {
            "talk_time": round(self.talk_time, 1),
            "turns": self.turn_count,
            "avg_turn_length": round(self.avg_turn_length, 1),
            "percentage": round(self.percentage, 1),
        }


@dataclass
class MeetingAnalytics:
    """Complete analytics for a meeting session."""
    speakers: dict[str, SpeakerStats] = field(default_factory=dict)
    total_duration: float = 0.0
    total_talk_time: float = 0.0
    silence_ratio: float = 0.0
    segment_count: int = 0
    topics: list[dict] = field(default_factory=list)  # from Claude analysis

    def to_dict(self) -> dict:
        return {
            "speakers": {k: v.to_dict() for k, v in self.speakers.items()},
            "total_duration": round(self.total_duration, 1),
            "total_talk_time": round(self.total_talk_time, 1),
            "silence_ratio": round(self.silence_ratio, 2),
            "segment_count": self.segment_count,
            "topics": self.topics,
        }


def compute_analytics(
    segments: list[Segment],
    total_duration: float,
) -> MeetingAnalytics:
    """Compute meeting analytics from diarized segments.

    Args:
        segments: List of Segment objects (with speaker labels from F1).
        total_duration: Total audio duration in seconds.

    Returns:
        MeetingAnalytics with all computed metrics.
    """
    analytics = MeetingAnalytics(
        total_duration=total_duration,
        segment_count=len(segments),
    )

    if not segments:
        return analytics

    # --- Per-speaker accumulation ---
    # Track turns: a "turn" is a contiguous run of segments by the same speaker
    speaker_times: dict[str, float] = {}
    speaker_turns: dict[str, int] = {}

    prev_speaker = None
    for seg in segments:
        label = seg.speaker or "Unknown"
        duration = max(0.0, seg.end - seg.start)

        speaker_times[label] = speaker_times.get(label, 0.0) + duration

        # Count a new turn when the speaker changes
        if label != prev_speaker:
            speaker_turns[label] = speaker_turns.get(label, 0) + 1
            prev_speaker = label

    total_talk = sum(speaker_times.values())
    analytics.total_talk_time = total_talk

    # Silence ratio
    if total_duration > 0:
        analytics.silence_ratio = max(0.0, 1.0 - (total_talk / total_duration))
    else:
        analytics.silence_ratio = 0.0

    # Build SpeakerStats
    for label in sorted(speaker_times.keys()):
        talk = speaker_times[label]
        turns = speaker_turns.get(label, 1)
        pct = (talk / total_talk * 100) if total_talk > 0 else 0.0
        avg_turn = talk / turns if turns > 0 else 0.0

        analytics.speakers[label] = SpeakerStats(
            talk_time=talk,
            turn_count=turns,
            avg_turn_length=avg_turn,
            percentage=pct,
        )

    return analytics


def extract_topics_from_analysis(analysis_text: str) -> list[dict]:
    """Extract topic names from the Claude analysis markdown.

    Looks for the "## Topics Discussed" section and parses bullet items.
    Returns a list of {"name": "Topic Name"} dicts.

    This is a best-effort parser — if the section doesn't exist or
    the format is unexpected, returns an empty list.
    """
    topics = []
    in_topics_section = False

    for line in analysis_text.split("\n"):
        stripped = line.strip()

        # Detect section headers
        if stripped.startswith("## "):
            if "topic" in stripped.lower():
                in_topics_section = True
                continue
            else:
                if in_topics_section:
                    break  # Left the topics section
                continue

        if stripped == "---" and in_topics_section:
            break

        if in_topics_section and stripped.startswith("- "):
            topic_name = stripped[2:].strip().rstrip(".")
            # Remove leading bold markers if present
            if topic_name.startswith("**") and "**" in topic_name[2:]:
                # Extract text inside **...**
                end = topic_name.index("**", 2)
                topic_name = topic_name[2:end]
            if topic_name:
                topics.append({"name": topic_name})

    return topics
