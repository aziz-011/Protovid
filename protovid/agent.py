"""The pipeline: .docx protocol -> storyboard -> slides + voice -> MP4."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .docx_reader import read_protocol
from .images import ImageAsset
from .slides import render_scene
from .storyboard import Storyboard, claude_storyboard, heuristic_storyboard
from .video import PAD_SECONDS, audio_duration, check_ffmpeg, concat, make_clip
from .voice import estimate_seconds, synthesize


def build_storyboard(docx_path: Path, *, use_ai: bool = True, language: str | None = None,
                     audience: str | None = None,
                     images: list[ImageAsset] | None = None) -> Storyboard:
    protocol = read_protocol(docx_path)
    if use_ai:
        return claude_storyboard(protocol, language=language, audience=audience, images=images)
    return heuristic_storyboard(protocol, images=images)


def render_video(board: Storyboard, out_path: Path, *, voice: str = "gtts", lang: str = "en",
                 work_dir: Path | None = None, log: Callable[[str], None] = print) -> Path:
    check_ffmpeg()
    out_path = Path(out_path)
    tmp = None
    if work_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="protovid-")
        work_dir = Path(tmp.name)
    work_dir.mkdir(parents=True, exist_ok=True)

    clips: list[Path] = []
    total = len(board.scenes)
    for i, scene in enumerate(board.scenes):
        log(f"[{i + 1}/{total}] {scene.kind:<9} {scene.heading}")
        photo = None
        if scene.image and board.images_dir:
            candidate = Path(board.images_dir) / scene.image
            if candidate.is_file():
                photo = candidate
                log(f"  image: {scene.image}")
            else:
                log(f"  ! image not found, skipping: {candidate}")
        slide = render_scene(scene, i, total, board.title, work_dir / f"slide_{i:03d}.png", photo=photo)
        try:
            audio = synthesize(scene.narration, work_dir / f"voice_{i:03d}.mp3", backend=voice, lang=lang)
        except Exception as exc:  # network down, TTS quota, missing engine...
            log(f"  ! narration failed ({exc.__class__.__name__}: {exc}); using a silent slide")
            audio = None
        seconds = (audio_duration(audio) if audio else estimate_seconds(scene.narration)) + PAD_SECONDS
        clips.append(make_clip(slide, audio, seconds, work_dir / f"clip_{i:03d}.mp4"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat(clips, out_path)
    if tmp is not None:
        tmp.cleanup()
    return out_path
