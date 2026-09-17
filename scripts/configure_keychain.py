"""Securely save or replace the MiniMax key in macOS Keychain."""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys


def main() -> int:
    security = shutil.which("security")
    if not security:
        print("macOS security CLI is unavailable; use MINIMAX_API_KEY in the environment.", file=sys.stderr)
        return 1
    key = getpass.getpass("MiniMax API Key (input hidden): ").strip()
    if not key:
        print("No key entered.", file=sys.stderr)
        return 1
    try:
        subprocess.run([
            security, "add-generic-password", "-a", "voice-clone-video",
            "-s", "voice-clone-video", "-w", key, "-U",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "Keychain rejected the update").strip()
        print(f"Keychain save failed: {detail}", file=sys.stderr)
        return 1
    print("Saved MiniMax credential to macOS Keychain: service=voice-clone-video")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
