"""Narration: text-to-speech backends.

* ``gtts``   - Google Translate TTS (needs internet, no key, many languages).
* ``festival`` - offline, needs Festival + a voice (Linux: ``apt install festival
  festvox-us-slt-hts``). English only; set the voice with PROTOVID_FESTIVAL_VOICE.
* ``espeak`` - offline, robotic, needs ``espeak-ng`` (uses an MBROLA voice such as
  ``mb-us2`` / ``mb-fr1`` when installed; set PROTOVID_ESPEAK_VOICE).
* ``none``   - silent video; each slide is shown for an estimated reading time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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
    if backend == "festival":
        exe = shutil.which("text2wave")
        if not exe:
            raise RuntimeError("Festival is not installed (needs the text2wave command)")
        voice = os.environ.get("PROTOVID_FESTIVAL_VOICE", "cmu_us_slt_arctic_hts")
        wav = out_mp3.with_suffix(".wav")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            subprocess.run([exe, "-eval", f"(voice_{voice})", f.name, "-o", str(wav)],
                           check=True, capture_output=True)
        finally:
            os.unlink(f.name)
        return wav
    if backend == "espeak":
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise RuntimeError("espeak-ng is not installed")
        voice = os.environ.get("PROTOVID_ESPEAK_VOICE") or _espeak_voice(exe, lang)
        wav = out_mp3.with_suffix(".wav")
        subprocess.run([exe, "-v", voice, "-s", "150", "-w", str(wav), text], check=True, capture_output=True)
        return wav
    raise ValueError(f"Unknown voice backend: {backend}")


def _espeak_voice(exe: str, lang: str) -> str:
    """Prefer an installed MBROLA voice for the language (more natural than plain eSpeak)."""
    try:
        out = subprocess.run([exe, "--voices=mb"], capture_output=True, text=True).stdout
    except OSError:
        return lang
    prefix = {"en": "en", "fr": "fr", "es": "es", "de": "de", "it": "it"}.get(lang[:2], lang)
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 5 and cols[1].startswith(prefix) and cols[4].startswith("mb/"):
            return cols[4].split("/", 1)[1]
    return lang
