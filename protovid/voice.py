"""Narration: text-to-speech backends.

* ``gtts``   - Google Translate TTS (needs internet, no key, many languages).
* ``espeak`` - offline, robotic, needs ``espeak-ng`` installed.
* ``none``   - silent video; each slide is shown for an estimated reading time.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def estimate_seconds(text: str, wpm: int = 150) -> float:
    return max(3.0, len(text.split()) / wpm * 60 + 1.0)


def synthesize(text: str, out_mp3: Path, backend: str = "gtts", lang: str = "en") -> Path | None:
    """Write narration audio to ``out_mp3``. Returns None for silent mode."""
    if backend == "none" or not text.strip():
        return None
    if backend == "gtts":
        from gtts import gTTS

        gTTS(text=text, lang=lang).save(str(out_mp3))
        return out_mp3
    if backend == "espeak":
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise RuntimeError("espeak-ng is not installed")
        wav = out_mp3.with_suffix(".wav")
        subprocess.run([exe, "-v", lang, "-w", str(wav), text], check=True)
        return wav
    raise ValueError(f"Unknown voice backend: {backend}")
