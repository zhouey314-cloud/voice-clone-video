"""CLI orchestrator for authorized MiniMax voice cloning and video dubbing."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from audio_qa import AudioQualityError, copy_read_only, normalize_reference, sha256_file, validate_reference
from minimax_client import MiniMaxClient, MiniMaxError
from subtitle_timeline import SubtitleError, measured_timeline, parse_subtitles, validate_cues, write_ass, write_srt
from video_mux import VideoError, assemble_narration, mux_video, probe_video


class ConsentError(RuntimeError):
    code = "VOICE_CLONE_CONSENT_REQUIRED"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_consent(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_file():
        raise ConsentError(f"Consent file not found: {source}")
    try:
        consent = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConsentError("Consent file is not valid JSON") from exc
    required = ("voice_owner", "authorized_by", "authorized_at", "allowed_use", "expiry", "source")
    if consent.get("consent_status") != "APPROVED" or any(not consent.get(key) for key in required):
        raise ConsentError("consent_status must be APPROVED and all authorization fields must be filled")
    allowed = " ".join(map(str, consent.get("allowed_use", []))).lower()
    if not any(term in allowed for term in ("synthetic", "narration", "配音", "合成", "video", "视频")):
        raise ConsentError("allowed_use does not cover synthetic narration/video")
    expiry = str(consent.get("expiry", ""))
    if expiry.lower() not in {"no-expiry", "none", "永久", "永不过期"}:
        try:
            if dt.date.fromisoformat(expiry) < dt.date.today():
                raise ConsentError("voice consent has expired")
        except ValueError as exc:
            raise ConsentError("expiry must be YYYY-MM-DD or no-expiry") from exc
    return consent


def _voice_id(value: str | None) -> str:
    if value:
        return value
    return "VoiceClone_" + dt.datetime.now().strftime("%Y%m%d%H%M%S")


def _mock_segment(path: Path, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=32000:cl=mono", "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(path)],
                   check=True, capture_output=True, text=True)


def _audio_duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    return float((json.loads(result.stdout).get("format") or {}).get("duration") or 0)


def _write_report(run_dir: Path, status: str, details: list[str], mock: bool = False) -> None:
    label = "DEMO_ONLY" if mock else status
    text = [f"# Voice Clone Video Run", "", f"Status: `{label}`", "", "## Evidence"]
    text.extend(f"- {item}" for item in details)
    text.extend(["", "## Review boundary", "", "This Skill never auto-publishes. Listen to the narration and watch the MP4 before sharing."])
    if mock:
        text.append("Mock audio is silent test material and is not evidence of a real voice clone.")
    (run_dir / "run_report.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def clone(args: argparse.Namespace) -> dict:
    run_dir = Path(args.output).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    consent = _load_consent(args.consent)
    report = validate_reference(args.audio)
    _json_write(run_dir / "voice_reference_report.json", report)
    if report["status"] != "PASS":
        raise AudioQualityError("; ".join(report["issues"]))
    reference_copy = copy_read_only(args.audio, run_dir / "source" / Path(args.audio).name)
    normalized = normalize_reference(reference_copy, run_dir / "source" / "normalized.wav")
    _json_write(run_dir / "voice_consent.json", consent)
    voice_id = _voice_id(args.voice_id)
    if args.mock:
        provider_result = {"provider_status": "MOCK", "voice_id": voice_id, "file_id": None}
    else:
        client = MiniMaxClient(tts_model=args.model)
        provider_result = client.clone_voice(reference_copy, voice_id, preview_text=args.preview_text,
                                             prompt_audio=args.prompt_audio, prompt_text=args.prompt_text)
        provider_result["provider_status"] = "REAL"
    profile = {
        "schema_version": "1.0",
        "provider": "MiniMax",
        "provider_status": provider_result["provider_status"],
        "voice_id": voice_id,
        "model": args.model,
        "created_at": _now(),
        "reference_sha256": sha256_file(reference_copy),
        "consent_sha256": sha256_file(args.consent),
        "normalized_reference": str(normalized.relative_to(run_dir)),
        "reference_report": "voice_reference_report.json",
        "consent_status": consent["consent_status"],
        "voice_owner": consent["voice_owner"],
        "preview_available": bool(args.preview_text),
    }
    if not args.mock:
        profile["provider_file_id"] = provider_result.get("file_id")
        profile["provider_trace"] = provider_result.get("response", {}).get("trace_id")
    _json_write(run_dir / "voice_profile.json", profile)
    _write_report(run_dir, "VOICE_READY", [f"voice_id={voice_id}", "reference audio validated", "consent approved"], args.mock)
    return profile


def dub(args: argparse.Namespace) -> dict:
    run_dir = Path(args.output).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    profile_path = Path(args.voice_profile)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    voice_id = profile.get("voice_id")
    if not voice_id:
        raise MiniMaxError("voice_profile.json has no voice_id")
    if profile.get("consent_status") != "APPROVED":
        raise ConsentError("voice_profile.json does not contain approved voice consent")
    if profile.get("provider_status") == "MOCK" and not args.mock:
        raise MiniMaxError("A MOCK voice profile cannot be used for a real run")
    if profile_path.resolve() != (run_dir / "voice_profile.json").resolve():
        shutil.copy2(profile_path, run_dir / "voice_profile.json")
    profile_consent = profile_path.parent / "voice_consent.json"
    if profile_consent.is_file() and not (run_dir / "voice_consent.json").is_file():
        shutil.copy2(profile_consent, run_dir / "voice_consent.json")
    profile_report = profile_path.parent / str(profile.get("reference_report", "voice_reference_report.json"))
    if profile_report.is_file() and not (run_dir / "voice_reference_report.json").is_file():
        shutil.copy2(profile_report, run_dir / "voice_reference_report.json")
    video_info = probe_video(args.video)
    cues = parse_subtitles(args.subtitles)
    validate_cues(cues, video_info["duration_seconds"])
    client = None if args.mock else MiniMaxClient(tts_model=args.model)
    segment_paths: list[Path] = []
    durations: list[float] = []
    segment_meta: list[dict] = []
    for index, cue in enumerate(cues, 1):
        if args.mock:
            duration = min(cue.end - cue.start - 0.05, max(0.2, 0.055 * len(cue.text)))
            segment = run_dir / "audio_segments" / f"{index:04d}.wav"
            _mock_segment(segment, duration)
            response_meta = {"provider_status": "MOCK"}
        else:
            assert client is not None
            result = client.synthesize(cue.text, voice_id, model=args.model)
            segment = run_dir / "audio_segments" / f"{index:04d}.mp3"
            segment.parent.mkdir(parents=True, exist_ok=True)
            segment.write_bytes(result["audio_bytes"])
            response_meta = {"provider_status": "REAL", "trace_id": result.get("response", {}).get("trace_id")}
        segment_paths.append(segment)
        durations.append(_audio_duration(segment))
        segment_meta.append(response_meta)
    timeline = measured_timeline(cues, durations, video_info["duration_seconds"])
    for item, path, meta in zip(timeline, segment_paths, segment_meta):
        item["audio_path"] = str(path.relative_to(run_dir))
        item.update(meta)
    timeline_doc = {"schema_version": "1.0", "provider": "MiniMax", "voice_id": voice_id,
                    "video_duration_seconds": video_info["duration_seconds"], "segments": timeline,
                    "provider_status": "MOCK" if args.mock else "REAL"}
    _json_write(run_dir / "narration_timeline.json", timeline_doc)
    narration = assemble_narration(segment_paths, timeline, video_info["duration_seconds"], run_dir / "narration.wav")
    srt = write_srt(timeline, run_dir / "dubbed.srt")
    ass = write_ass(timeline, run_dir / "dubbed.ass")
    output_video = mux_video(args.video, narration, ass, run_dir / "dubbed.mp4", args.subtitle_mode)
    status = "DEMO_ONLY" if args.mock else "VIDEO_READY_FOR_REVIEW"
    _write_report(run_dir, status, [f"segments={len(timeline)}", f"video={output_video.name}", f"subtitles={srt.name}"], args.mock)
    return {"status": status, "voice_id": voice_id, "video": str(output_video), "timeline": str(run_dir / "narration_timeline.json")}


def run(args: argparse.Namespace) -> dict:
    profile = clone(args)
    args.voice_profile = str(Path(args.output).resolve() / "voice_profile.json")
    return dub(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorized MiniMax voice clone and subtitle video dubbing")
    sub = parser.add_subparsers(dest="command", required=True)
    def add_common(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--output", required=True)
        command_parser.add_argument("--model", default=os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"))
        command_parser.add_argument("--mock", action="store_true")
    clone_parser = sub.add_parser("clone")
    clone_parser.add_argument("--audio", required=True)
    clone_parser.add_argument("--consent", required=True)
    clone_parser.add_argument("--voice-id")
    clone_parser.add_argument("--preview-text")
    clone_parser.add_argument("--prompt-audio")
    clone_parser.add_argument("--prompt-text")
    add_common(clone_parser)
    dub_parser = sub.add_parser("dub")
    dub_parser.add_argument("--voice-profile", required=True)
    dub_parser.add_argument("--video", required=True)
    dub_parser.add_argument("--subtitles", required=True)
    dub_parser.add_argument("--subtitle-mode", choices=["burn", "mux"], default="burn")
    add_common(dub_parser)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--audio", required=True)
    run_parser.add_argument("--consent", required=True)
    run_parser.add_argument("--video", required=True)
    run_parser.add_argument("--subtitles", required=True)
    run_parser.add_argument("--voice-id")
    run_parser.add_argument("--preview-text")
    run_parser.add_argument("--prompt-audio")
    run_parser.add_argument("--prompt-text")
    run_parser.add_argument("--subtitle-mode", choices=["burn", "mux"], default="burn")
    add_common(run_parser)
    args = parser.parse_args()
    try:
        result = {"status": "VOICE_READY"} if args.command == "clone" else None
        if args.command == "clone":
            result = clone(args)
        elif args.command == "dub":
            result = dub(args)
        else:
            result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ConsentError, AudioQualityError, MiniMaxError, SubtitleError, VideoError) as exc:
        print(json.dumps({"status": getattr(exc, "code", "BLOCKED"), "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
