"""Small dependency-free MiniMax Voice Clone and T2A HTTP client."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def _load_local_env() -> dict:
    """Read optional local-only settings from config.local.env.

    The file is intentionally untracked and must never be included in a public
    Skill package. Environment variables and macOS Keychain remain supported.
    """
    env: dict = {}
    skill_dir = Path(__file__).resolve().parents[1]
    cfg = skill_dir / "config.local.env"
    if not cfg.exists():
        return env
    for raw in cfg.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env



class MiniMaxError(RuntimeError):
    code = "BLOCKED_EXTERNAL"


def _safe_detail(value: object) -> str:
    text = str(value)
    for secret_name in ("MINIMAX_API_KEY", "TTS_API_KEY"):
        secret = os.environ.get(secret_name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[-1000:]


def load_api_key(explicit: str | None = None) -> str:
    """Read the key from local config file, explicit value, env, or macOS Keychain.

    Priority: config.local.env (internal, travels with skill) > explicit arg >
    MINIMAX_API_KEY / TTS_API_KEY env > macOS Keychain.
    """
    if explicit:
        return explicit
    local = _load_local_env()
    if local.get("MINIMAX_API_KEY"):
        return local["MINIMAX_API_KEY"]
    for name in ("MINIMAX_API_KEY", "TTS_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    security = shutil.which("security")
    if not security:
        return ""
    try:
        result = subprocess.run(
            [security, "find-generic-password", "-a", "voice-clone-video",
             "-s", "voice-clone-video", "-w"],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


class MiniMaxClient:
    def __init__(self, api_key: str | None = None, api_host: str | None = None,
                 tts_model: str | None = None, timeout: int = 120):
        local = _load_local_env()
        self.api_key = load_api_key(api_key)
        self.api_host = (api_host
                         or local.get("MINIMAX_API_HOST")
                         or os.environ.get("MINIMAX_API_HOST", "https://api.minimaxi.com")).rstrip("/")
        self.tts_model = (tts_model
                          or local.get("MINIMAX_TTS_MODEL")
                          or os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"))
        self.timeout = timeout
        if not self.api_key:
            raise MiniMaxError("MINIMAX_API_KEY is not configured")

    def _request(self, path: str, data: bytes, content_type: str) -> dict:
        request = urllib.request.Request(
            f"{self.api_host}{path}", data=data,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": content_type},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _safe_detail(exc.read().decode("utf-8", errors="replace"))
            raise MiniMaxError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MiniMaxError(f"MiniMax request failed: {_safe_detail(exc)}") from exc
        base = payload.get("base_resp") or {}
        if str(base.get("status_code", "0")) not in {"0", "None"}:
            raise MiniMaxError(f"MiniMax API error: {_safe_detail(base.get('status_msg') or payload)}")
        return payload

    def upload(self, source: str | Path, purpose: str) -> dict:
        boundary = f"----voice-clone-{uuid.uuid4().hex}"
        path = Path(source)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        return self._request("/v1/files/upload", b"".join(chunks), f"multipart/form-data; boundary={boundary}")

    def clone_voice(self, source: str | Path, voice_id: str, preview_text: str | None = None,
                    prompt_audio: str | Path | None = None, prompt_text: str | None = None,
                    model: str = "speech-2.8-hd") -> dict:
        if len(voice_id) < 8 or len(voice_id) > 256 or not voice_id[0].isalpha():
            raise MiniMaxError("voice_id must be 8-256 characters and start with a letter")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if any(char not in allowed for char in voice_id) or voice_id[-1] in "-_":
            raise MiniMaxError("voice_id contains invalid characters")
        uploaded = self.upload(source, "voice_clone")
        file_id = (uploaded.get("file") or {}).get("file_id")
        if file_id is None:
            raise MiniMaxError("MiniMax upload returned no file_id")
        payload: dict[str, object] = {"file_id": file_id, "voice_id": voice_id, "model": model}
        if preview_text:
            if len(preview_text) > 1000:
                raise MiniMaxError("preview_text must be at most 1000 characters")
            payload["text"] = preview_text
        if prompt_audio is not None or prompt_text is not None:
            if prompt_audio is None or not prompt_text:
                raise MiniMaxError("prompt_audio and prompt_text must be supplied together")
            prompt_upload = self.upload(prompt_audio, "prompt_audio")
            prompt_id = (prompt_upload.get("file") or {}).get("file_id")
            if prompt_id is None:
                raise MiniMaxError("MiniMax prompt upload returned no file_id")
            payload["clone_prompt"] = {"prompt_audio": prompt_id, "prompt_text": prompt_text}
        result = self._request("/v1/voice_clone", json.dumps(payload, ensure_ascii=False).encode(), "application/json")
        return {"response": result, "file_id": file_id, "voice_id": voice_id}

    def synthesize(self, text: str, voice_id: str, speed: float = 1.0,
                   emotion: str | None = None, pronunciation: list[str] | None = None,
                   model: str | None = None) -> dict:
        if not text.strip():
            raise MiniMaxError("TTS text must not be empty")
        payload: dict[str, object] = {
            "model": model or self.tts_model,
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1, "pitch": 0},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "subtitle_enable": True,
            "subtitle_type": "word",
            "output_format": "hex",
            "language_boost": "Chinese",
        }
        if emotion:
            payload["voice_setting"]["emotion"] = emotion  # type: ignore[index]
        if pronunciation:
            payload["pronunciation_dict"] = {"tone": pronunciation}
        result = self._request("/v1/t2a_v2", json.dumps(payload, ensure_ascii=False).encode(), "application/json")
        audio = ((result.get("data") or {}).get("audio") or "").strip()
        if not audio:
            raise MiniMaxError("MiniMax TTS returned no audio")
        try:
            audio_bytes = bytes.fromhex(audio)
        except ValueError:
            try:
                audio_bytes = base64.b64decode(audio)
            except Exception as exc:
                raise MiniMaxError("MiniMax TTS returned invalid audio data") from exc
        return {"audio_bytes": audio_bytes, "response": result}
