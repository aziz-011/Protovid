"""The pipeline: .docx protocol -> storyboard -> slides + voice -> MP4."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .docx_reader import read_protocol
from .images import ImageAsset, from_figures, merge
from .slides import render_scene
from .storyboard import Storyboard, claude_storyboard, heuristic_storyboard
from .video import PAD_SECONDS, audio_duration, check_ffmpeg, concat, make_clip
from .voice import estimate_seconds, synthesize


def build_storyboard(docx_path: Path, *, use_ai: bool = True, language: str | None = None,
                     audience: str | None = None,
                     images: list[ImageAsset] | None = None,
                     figures_dir: Path | None = None,
                     log: Callable[[str], None] = print) -> Storyboard:
    """``figures_dir``: where to save pictures embedded in the .docx (None = ignore them)."""
    protocol = read_protocol(docx_path, figures_dir=figures_dir)
    doc_images = from_figures(protocol.figures)
    if doc_images:
        log(f"Pictures found in the document: {len(doc_images)} (saved to {figures_dir})")
    all_images = merge(doc_images, images or [])
    if use_ai:
        return claude_storyboard(protocol, language=language, audience=audience, images=all_images)
    return heuristic_storyboard(protocol, images=all_images)


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
        photo = board.image_path(scene.image)
        if scene.image and photo is None:
            log(f"  ! image not found, skipping: {scene.image}")
        elif photo is not None:
            log(f"  image: {scene.image}")
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
