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


def make_clip(slide: Path, audio: Path | None, seconds: float, out: Path,
              anim_frames: Path | None = None, anim_fps: int = 24,
              anim_xy: tuple[int, int] = (0, 0),
              foreground: Path | None = None) -> Path:
    """One clip per slide. ``anim_frames``: folder of f0000.png... RGBA frames looped over the slide.

    ``foreground``: full-frame RGBA PNG laid over the animation (labels, photo inset).
    """
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(slide)]
    if audio is not None:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if anim_frames is not None:
        cmd += ["-stream_loop", "-1", "-framerate", str(anim_fps), "-i", str(anim_frames / "f%04d.png")]
        x, y = anim_xy
        graph = f"[2:v]format=rgba[m];[0:v][m]overlay={x}:{y}:shortest=0:eof_action=repeat"
        if foreground is not None:
            cmd += ["-loop", "1", "-framerate", "30", "-i", str(foreground)]
            graph += "[a];[a][3:v]overlay=0:0"
        graph += ",fps=30,fade=t=in:st=0:d=0.4,format=yuv420p[v]"
        video_args = ["-filter_complex", graph, "-map", "[v]", "-map", "1:a"]
    else:
        video_args = ["-vf", "fade=t=in:st=0:d=0.4,format=yuv420p"]
    cmd += [
        "-t", f"{seconds:.2f}",
        *video_args,
        "-af", f"apad,atrim=0:{seconds:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", *([] if anim_frames else ["-tune", "stillimage"]), "-r", "30",
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
