from __future__ import annotations

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audio_qa import AudioQualityError, validate_reference
from run_voice_clone_video import ConsentError, _load_consent
from subtitle_timeline import Cue, SubtitleError, measured_timeline, parse_subtitles
from validate_run import validate_skill


def make_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    frames = b"\x00\x00" * int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


class CoreTests(unittest.TestCase):
    def test_skill_installation_is_valid(self):
        result = validate_skill(ROOT)
        self.assertEqual(result["status"], "PASS", result)

    def test_consent_must_be_approved_and_cover_use(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consent.json"
            path.write_text(json.dumps({"consent_status": "WAITING_CONFIRMATION"}), encoding="utf-8")
            with self.assertRaises(ConsentError):
                _load_consent(path)

    def test_reference_under_ten_seconds_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.wav"
            make_wav(path, 1)
            report = validate_reference(path)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertIn("duration_below_10s", report["issues"])

    def test_srt_parse_and_timeline_does_not_average_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:01,500\n你好\n\n2\n00:00:02,000 --> 00:00:04,500\n这是第二句\n", encoding="utf-8")
            cues = parse_subtitles(path)
            timeline = measured_timeline(cues, [0.4, 1.2], 5)
            self.assertEqual([item["start_ms"] for item in timeline], [0, 2000])
            self.assertEqual([item["duration_ms"] for item in timeline], [400, 1200])

    def test_timeline_blocks_audio_overrun(self):
        cues = [Cue("1", 0, 1, "一句话")]
        with self.assertRaises(SubtitleError):
            measured_timeline(cues, [1.5], 2)


if __name__ == "__main__":
    unittest.main()
