"""FFmpeg assembly for timed narration and an existing video."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class VideoError(RuntimeError):
    code = "BLOCKED_SEGMENT_AUDIO_ALIGNMENT"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise VideoError(f"Missing executable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "ffmpeg failed").strip()[-1000:]
        raise VideoError(detail) from exc


def probe_video(path: str | Path) -> dict:
    result = _run(["ffprobe", "-v", "error", "-show_entries",
                   "format=duration:stream=index,codec_type,codec_name,width,height",
                   "-of", "json", str(path)])
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration_seconds": float((data.get("format") or {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "has_audio": bool(audio),
    }


def assemble_narration(segment_paths: list[str | Path], timeline: list[dict],
                       video_duration: float, destination: str | Path) -> Path:
    if len(segment_paths) != len(timeline) or not segment_paths:
        raise VideoError("Narration segments and timeline do not match")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in segment_paths:
        command.extend(["-i", str(path)])
    filters = []
    labels = []
    for index, item in enumerate(timeline):
        delay = max(0, int(item["start_ms"]))
        label = f"a{index}"
        filters.append(f"[{index}:a]adelay={delay}|{delay}[{label}]")
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,aresample=48000,apad[mix]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[mix]",
                    "-t", f"{video_duration:.3f}", "-ar", "48000", "-ac", "2",
                    "-c:a", "pcm_s16le", str(target)])
    _run(command)
    return target


def mux_video(video: str | Path, narration: str | Path, ass: str | Path,
              destination: str | Path, subtitle_mode: str = "burn") -> Path:
    if subtitle_mode not in {"burn", "mux"}:
        raise VideoError("subtitle_mode must be burn or mux")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    video_info = probe_video(video)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(narration)]
    if subtitle_mode == "burn":
        # The ASS file is generated locally and is not interpreted as shell text.
        escaped = str(Path(ass).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        command.extend(["-vf", f"subtitles='{escaped}'"])
    command.extend(["-map", "0:v:0", "-map", "1:a:0", "-t", f"{video_info['duration_seconds']:.3f}",
                    "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-map_metadata", "-1", str(target)])
    _run(command)
    return target
