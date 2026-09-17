from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))


class MockE2ETests(unittest.TestCase):
    def test_mock_run_creates_timed_video_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "reference.wav"
            video = root / "input.mp4"
            subtitles = root / "input.srt"
            consent = root / "voice_consent.json"
            output = root / "run"
            make_wav(audio, 30)
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=black:s=320x180:r=25", "-t", "5", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-an", str(video)], check=True)
            subtitles.write_text("1\n00:00:00,000 --> 00:00:01,500\n第一句测试\n\n2\n00:00:02,000 --> 00:00:04,500\n第二句测试\n", encoding="utf-8")
            consent.write_text(json.dumps({
                "consent_status": "APPROVED", "voice_owner": "Test Owner", "authorized_by": "Test Owner",
                "authorized_at": "2026-09-07", "allowed_use": ["AI synthetic narration for video"],
                "expiry": "no-expiry", "source": {"recording_provided_by": "Test Owner"}
            }), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/run_voice_clone_video.py"), "run",
                       "--audio", str(audio), "--consent", str(consent), "--video", str(video),
                       "--subtitles", str(subtitles), "--subtitle-mode", "mux", "--mock", "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertIn("DEMO_ONLY", (output / "run_report.md").read_text(encoding="utf-8"))
            self.assertTrue((output / "dubbed.mp4").is_file())
            self.assertTrue((output / "narration.wav").is_file())
            timeline = json.loads((output / "narration_timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(len(timeline["segments"]), 2)
            self.assertEqual(timeline["provider_status"], "MOCK")
            self.assertIn("DEMO_ONLY", result.stdout)


if __name__ == "__main__":
    unittest.main()
