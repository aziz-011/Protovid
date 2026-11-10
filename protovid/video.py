"""Assemble slides + narration into an MP4 with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PAD_SECONDS = 0.8  # breathing room after each narration


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{proc.stderr[-2000:]}")


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def make_clip(slide: Path, audio: Path | None, seconds: float, out: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(slide)]
    if audio is not None:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-t", f"{seconds:.2f}",
        "-vf", "fade=t=in:st=0:d=0.4,format=yuv420p",
        "-af", f"apad,atrim=0:{seconds:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k",
        "-shortest", str(out),
    ]
    _run(cmd)
    return out


def concat(clips: list[Path], out: Path) -> Path:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c", "copy", "-movflags", "+faststart", str(out)])
    listing.unlink(missing_ok=True)
    return out


def check_ffmpeg() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise RuntimeError("ffmpeg/ffprobe not found. Install ffmpeg and make sure it is on PATH.")
