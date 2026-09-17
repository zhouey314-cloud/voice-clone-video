"""Validate a Skill installation or a completed run without external calls."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


REQUIRED_SKILL_FILES = [
    "SKILL.md", "README.md", "references/voice_consent_template.json",
    "scripts/run_voice_clone_video.py", "scripts/minimax_client.py",
    "scripts/configure_keychain.py", "scripts/audio_qa.py", "scripts/subtitle_timeline.py", "scripts/video_mux.py",
]
SECRET_PATTERNS = [re.compile(r"sk-[A-Za-z0-9]{16,}"), re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}")]


def validate_skill(skill_dir: str | Path) -> dict:
    root = Path(skill_dir)
    missing = [item for item in REQUIRED_SKILL_FILES if not (root / item).is_file()]
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8") if (root / "SKILL.md").is_file() else ""
    valid_frontmatter = skill_text.startswith("---\n") and "name: voice-clone-video" in skill_text.split("---", 2)[1]
    secret_hits = []
    for path in root.rglob("*"):
        if path.is_file() and path.name not in {"validate_run.py"}:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    secret_hits.append(str(path.relative_to(root)))
    return {"status": "PASS" if not missing and valid_frontmatter and not secret_hits else "FAIL",
            "missing": missing, "valid_frontmatter": valid_frontmatter,
            "secret_pattern_hits": sorted(set(secret_hits))}


def validate_run(run_dir: str | Path) -> dict:
    root = Path(run_dir)
    report: dict = {"status": "PASS", "issues": [], "checked": []}
    for name in ("voice_profile.json", "voice_reference_report.json", "narration_timeline.json", "run_report.md"):
        path = root / name
        report["checked"].append(name)
        if not path.is_file():
            report["issues"].append(f"missing:{name}")
    timeline_path = root / "narration_timeline.json"
    if timeline_path.is_file():
        try:
            data = json.loads(timeline_path.read_text(encoding="utf-8"))
            if not isinstance(data.get("segments"), list) or not data.get("segments"):
                report["issues"].append("timeline_segments_missing")
        except json.JSONDecodeError:
            report["issues"].append("timeline_invalid_json")
    video = root / "dubbed.mp4"
    if video.is_file():
        report["checked"].append("dubbed.mp4")
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                     "stream=codec_type,codec_name", "-of", "json", str(video)],
                                    check=True, capture_output=True, text=True)
            streams = json.loads(result.stdout).get("streams", [])
            types = {stream.get("codec_type") for stream in streams}
            codecs = {stream.get("codec_name") for stream in streams}
            if not {"video", "audio"}.issubset(types):
                report["issues"].append("video_or_audio_stream_missing")
            if "h264" not in codecs or "aac" not in codecs:
                report["issues"].append("unexpected_output_codec")
        except Exception as exc:  # pragma: no cover - executable presence is environment-specific.
            report["issues"].append(f"ffprobe_failed:{exc}")
    report["status"] = "PASS" if not report["issues"] else "FAIL"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir")
    parser.add_argument("--run-dir")
    args = parser.parse_args()
    if not args.skill_dir and not args.run_dir:
        parser.error("provide --skill-dir or --run-dir")
    result = validate_skill(args.skill_dir) if args.skill_dir else validate_run(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
