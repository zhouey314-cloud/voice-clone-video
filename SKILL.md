---
name: voice-clone-video
description: Clone an explicitly authorized person's voice from a short audio reference and use it to narrate subtitles over an existing video. Use this skill whenever the user mentions voice cloning, voice distillation, using someone's timbre to read captions, dubbing a video with a supplied recording, or generating a narrated MP4 from SRT/VTT subtitles. The default provider is MiniMax; the workflow preserves consent, creates one TTS audio segment per subtitle cue, validates timing, and never auto-publishes.
compatibility: Python 3.10+, ffmpeg, ffprobe, and a configured MiniMax API account for real voice cloning. On macOS the credential may be stored in Keychain. Offline mock mode is available only for tests and is always labelled DEMO_ONLY.
---

# Voice Clone Video

Use this Skill for a complete, auditable workflow:

```text
authorized reference audio
  -> audio QA
  -> MiniMax voice_id
  -> one TTS request per subtitle cue
  -> measured narration timeline
  -> video/audio mux and optional subtitle burn-in
  -> human review package
```

## Safety and consent gate

Before uploading or synthesizing any real person's voice, require a local
`voice_consent.json` with `consent_status: APPROVED`, the voice owner,
authorizer, authorization time, allowed use, expiry, and traceable source.
If it is missing, incomplete, expired, or does not cover synthetic narration
for the requested video, stop with `VOICE_CLONE_CONSENT_REQUIRED`.

Do not clone or use a voice for impersonation, fraud, identity verification,
OTP or account recovery, political persuasion, financial or medical deception,
or any other high-impact claim made to appear as the real person. Disclose the
result as an AI-generated voice where the platform or audience requires it.
Never put API keys, tokens, private identity documents, or raw secret material
in this Skill package or in reports.

## Inputs and outputs

The normal one-shot input is:

- authorized `mp3`, `m4a`, or `wav` reference audio; 30–60 seconds is a supported
  target, while the provider accepts 10 seconds through 5 minutes and up to
  20 MB;
- `voice_consent.json`;
- an existing `mp4`, `mov`, or other ffmpeg-readable video;
- an `srt` or `vtt` subtitle file containing the narration text and source cue
  timings.

The Skill writes each run under its requested output directory:

```text
source/reference.<ext>          original reference copy
source/normalized.wav           provider-ready work copy
voice_reference_report.json     audio QA and warnings
voice_profile.json              provider, voice_id, hashes, and status
audio_segments/0001.mp3        one generated file per cue
narration.wav                    timed narration track
narration_timeline.json          exact measured cue timeline
dubbed.srt / dubbed.ass          regenerated subtitle files
dubbed.mp4                       video with replacement narration
run_report.md                    status, evidence, warnings, next action
```

`dubbed.mp4` burns subtitles by default. Use `--subtitle-mode mux` to leave
subtitles as sidecar files and avoid a second subtitle layer when the source
video already contains burned-in text.

## Commands

Run from the installed Skill directory, or replace `python` with the user's
configured Python executable:

```bash
python scripts/run_voice_clone_video.py clone \
  --audio reference.m4a \
  --consent voice_consent.json \
  --output runs/my-voice

python scripts/run_voice_clone_video.py dub \
  --voice-profile runs/my-voice/voice_profile.json \
  --video input.mp4 \
  --subtitles input.srt \
  --output runs/my-video

python scripts/run_voice_clone_video.py run \
  --audio reference.m4a \
  --consent voice_consent.json \
  --video input.mp4 \
  --subtitles input.srt \
  --output runs/my-video
```

Use `--voice-id` to supply a unique MiniMax ID. Otherwise the command creates
one beginning with `VoiceClone_`. Use `--model` to override the default
`speech-2.8-turbo`, `--subtitle-mode mux|burn`, and `--mock` only for offline
tests. Mock results must remain labelled `DEMO_ONLY` and are not provider proof.

On macOS, configure the credential once without putting it in a file:

```bash
python scripts/configure_keychain.py
```

The client checks `MINIMAX_API_KEY` first and then reads the Keychain item
`service=voice-clone-video`, `account=voice-clone-video`.

## Execution rules

1. Resolve and validate paths before making an external call. Preserve the
   reference source and compute SHA-256 hashes for the reference and consent.
2. Run `audio_qa.py`. Reject unsupported format, duration, size, severe
   clipping, or unusable audio with `VOICE_REFERENCE_QUALITY_INSUFFICIENT`.
3. Upload the reference using MiniMax `purpose=voice_clone`, call the voice
   clone endpoint, and save the returned real `voice_id`. A generic speaker
   name or pitch shift is not a voice clone.
4. Parse SRT/VTT cues and reject malformed, empty, negative, overlapping, or
   out-of-video cues before synthesis.
5. Call TTS once for each cue with `subtitle_enable=true` and word-level
   subtitle support enabled. Apply pronunciation or emotion options only to
   the provider request; never silently rewrite the displayed subtitle.
6. Measure every returned audio segment with `ffprobe`. Place it at the
   corresponding source cue start. If it runs beyond that cue, overlaps the
   next cue, or exceeds the video, stop with
   `BLOCKED_SEGMENT_AUDIO_ALIGNMENT`; never split a full track by average
   sentence duration.
7. Assemble narration with silence gaps, mute the source audio by default,
   and render an H.264/AAC MP4. Keep the regenerated SRT/ASS and all evidence.
8. Stop at `VIDEO_READY_FOR_REVIEW` for a real run. The user must listen and
   watch the result before sharing or publishing; this Skill does not publish.

## Status contract

Use one of these statuses in `run_report.md` and JSON reports:

```text
WAITING_CONSENT
VOICE_REFERENCE_QUALITY_INSUFFICIENT
VOICE_READY
BLOCKED_EXTERNAL
BLOCKED_SEGMENT_AUDIO_ALIGNMENT
VIDEO_READY_FOR_REVIEW
DEMO_ONLY
```

Every blocked result must include the exact missing input or failed check and
one next action. A passing offline mock run is a software test, not evidence
that a real MiniMax voice was created.

## Verification

Run the bundled deterministic tests:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python scripts/validate_run.py --skill-dir .
```

For a real run, also inspect `run_report.md`, listen to `narration.wav`, watch
`dubbed.mp4`, and confirm the final subtitles do not drift, overlap, or show a
duplicate burned-in layer.
