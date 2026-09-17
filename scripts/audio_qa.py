"""Deterministic reference-audio validation and normalization."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".wav"}
MIN_SECONDS = 10.0
MAX_SECONDS = 300.0
MAX_BYTES = 20 * 1024 * 1024


class AudioQualityError(RuntimeError):
    code = "VOICE_REFERENCE_QUALITY_INSUFFICIENT"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise AudioQualityError(f"Missing executable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-500:]
        raise AudioQualityError(f"ffprobe failed: {detail}") from exc


def probe_audio(path: str | Path) -> dict:
    source = Path(path)
    result = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size,format_name:stream=codec_name,sample_rate,channels",
        "-of", "json", str(source),
    ])
    data = json.loads(result.stdout or "{}")
    stream = next((item for item in data.get("streams", []) if item.get("codec_name")), {})
    fmt = data.get("format", {})
    return {
        "path": str(source.resolve()),
        "bytes": int(source.stat().st_size),
        "duration_seconds": float(fmt.get("duration") or 0),
        "format_name": str(fmt.get("format_name") or ""),
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
    }


def _volume_metrics(path: Path) -> dict:
    try:
        result = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                       "-af", "volumedetect", "-f", "null", "-"])
    except AudioQualityError as exc:
        return {"volume_check": "UNKNOWN", "volume_error": str(exc)}
    text = result.stderr or ""
    max_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", text)
    mean_match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", text)
    max_db = float(max_match.group(1)) if max_match else None
    mean_db = float(mean_match.group(1)) if mean_match else None
    return {
        "max_volume_db": max_db,
        "mean_volume_db": mean_db,
        "clipping_risk": bool(max_db is not None and max_db >= -0.1),
        "volume_check": "PASS" if max_db is not None else "UNKNOWN",
    }


def validate_reference(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_file():
        raise AudioQualityError(f"Reference audio not found: {source}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise AudioQualityError("Supported reference formats are mp3, m4a, and wav")
    if source.stat().st_size > MAX_BYTES:
        raise AudioQualityError(f"Reference audio exceeds {MAX_BYTES} bytes")
    report = probe_audio(source)
    duration = report["duration_seconds"]
    issues: list[str] = []
    if duration < MIN_SECONDS:
        issues.append(f"duration_below_{MIN_SECONDS:g}s")
    if duration > MAX_SECONDS:
        issues.append(f"duration_above_{MAX_SECONDS:g}s")
    if not report["codec"] or not report["sample_rate"] or not report["channels"]:
        issues.append("missing_audio_stream_metadata")
    report.update(_volume_metrics(source))
    if report.get("clipping_risk"):
        issues.append("clipping_risk")
    report["reference_sha256"] = sha256_file(source)
    report["target_window"] = "30-60 seconds"
    report["status"] = "PASS" if not issues else "BLOCKED"
    report["issues"] = issues
    report["warnings"] = []
    if 10 <= duration < 30:
        report["warnings"].append("short_reference_below_30s_may_reduce_voice_stability")
    if duration > 60:
        report["warnings"].append("reference_above_target_60s_is_supported_but_not_required")
    if report.get("volume_check") == "UNKNOWN":
        report["warnings"].append("volume_metrics_unavailable")
    return report


def normalize_reference(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path),
            "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(destination_path),
        ], check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AudioQualityError("Missing ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioQualityError((exc.stderr or "ffmpeg normalization failed").strip()) from exc
    return destination_path


def copy_read_only(source: str | Path, destination: str | Path) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o444)
    return target
