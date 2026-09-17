"""SRT/VTT parsing and measured cue timeline generation."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path


class SubtitleError(RuntimeError):
    code = "BLOCKED_SEGMENT_AUDIO_ALIGNMENT"


@dataclass(frozen=True)
class Cue:
    cue_id: str
    start: float
    end: float
    text: str


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise SubtitleError(f"Invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _clean_text(lines: list[str]) -> str:
    text = " ".join(line.strip() for line in lines if line.strip())
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_subtitles(path: str | Path) -> list[Cue]:
    source = Path(path)
    raw = source.read_text(encoding="utf-8-sig")
    if source.suffix.lower() == ".vtt" or raw.lstrip().startswith("WEBVTT"):
        raw = re.sub(r"^WEBVTT[^\n]*\n", "", raw.lstrip(), count=1)
    blocks = re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    cues: list[Cue] = []
    for index, block in enumerate(blocks, 1):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].split("-->")
        if len(timing) != 2:
            raise SubtitleError(f"Malformed cue {index}")
        cue_id = lines[0] if timing_index > 0 and not re.match(r"^\d+$", lines[0]) else str(index)
        start = parse_timestamp(timing[0])
        end = parse_timestamp(timing[1].split()[0])
        text = _clean_text(lines[timing_index + 1:])
        cues.append(Cue(cue_id, start, end, text))
    if not cues:
        raise SubtitleError("No subtitle cues found")
    return cues


def validate_cues(cues: list[Cue], video_duration: float | None = None) -> None:
    previous_end = -1.0
    for cue in cues:
        if cue.start < 0 or cue.end <= cue.start:
            raise SubtitleError(f"Invalid cue interval: {cue.cue_id}")
        if not cue.text:
            raise SubtitleError(f"Empty subtitle cue: {cue.cue_id}")
        if cue.start < previous_end - 0.001:
            raise SubtitleError(f"Subtitle cues overlap at {cue.cue_id}")
        if video_duration is not None and cue.end > video_duration + 0.001:
            raise SubtitleError(f"Cue {cue.cue_id} exceeds video duration")
        previous_end = cue.end


def measured_timeline(cues: list[Cue], durations: list[float], video_duration: float,
                     tolerance: float = 0.05) -> list[dict]:
    validate_cues(cues, video_duration)
    if len(cues) != len(durations):
        raise SubtitleError("Cue and audio segment counts differ")
    timeline: list[dict] = []
    for index, (cue, duration) in enumerate(zip(cues, durations)):
        if duration <= 0:
            raise SubtitleError(f"Audio duration is empty for cue {cue.cue_id}")
        start = cue.start
        end = start + duration
        if end > cue.end + tolerance:
            raise SubtitleError(f"Generated audio exceeds source cue {cue.cue_id}")
        if index + 1 < len(cues) and end > cues[index + 1].start + tolerance:
            raise SubtitleError(f"Generated audio overlaps next cue after {cue.cue_id}")
        if end > video_duration + tolerance:
            raise SubtitleError(f"Generated audio exceeds video after {cue.cue_id}")
        timeline.append({
            "segment_id": cue.cue_id,
            "spoken_text": cue.text,
            "source_start_ms": round(cue.start * 1000),
            "source_end_ms": round(cue.end * 1000),
            "start_ms": round(start * 1000),
            "end_ms": round(end * 1000),
            "duration_ms": round(duration * 1000),
            "alignment_confidence": "measured_audio_duration",
        })
    return timeline


def _format_srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(timeline: list[dict], destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for index, item in enumerate(timeline, 1):
        blocks.append(f"{index}\n{_format_srt_time(item['start_ms'] / 1000)} --> "
                     f"{_format_srt_time(item['end_ms'] / 1000)}\n{item['spoken_text']}")
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return target


def _ass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _format_ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def write_ass(timeline: list[dict], destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,54,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,150,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = [f"Dialogue: 0,{_format_ass_time(item['start_ms'] / 1000)},{_format_ass_time(item['end_ms'] / 1000)},Default,,0,0,0,,{_ass_escape(item['spoken_text'])}" for item in timeline]
    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return target
