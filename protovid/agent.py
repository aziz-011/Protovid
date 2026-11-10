"""The pipeline: .docx protocol -> storyboard -> slides + voice -> MP4."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .docx_reader import read_protocol
from .images import ImageAsset, from_figures, merge
from .render3d import ModelLibrary, load_model, render_frames
from .slides import THEMES, panel_inner, render_panel_foreground, render_scene
from .storyboard import Storyboard, claude_storyboard, heuristic_storyboard
from .video import PAD_SECONDS, audio_duration, check_ffmpeg, concat, make_clip
from .voice import estimate_seconds, synthesize


def build_storyboard(docx_path: Path, *, use_ai: bool = True, language: str | None = None,
                     audience: str | None = None,
                     context: str | None = None,
                     images: list[ImageAsset] | None = None,
                     figures_dir: Path | None = None,
                     models: ModelLibrary | None = None,
                     log: Callable[[str], None] = print) -> Storyboard:
    """``figures_dir``: where to save pictures embedded in the .docx (None = ignore them)."""
    protocol = read_protocol(docx_path, figures_dir=figures_dir)
    doc_images = from_figures(protocol.figures)
    if doc_images:
        log(f"Pictures found in the document: {len(doc_images)} (saved to {figures_dir})")
    all_images = merge(doc_images, images or [])
    if use_ai:
        return claude_storyboard(protocol, language=language, audience=audience, context=context,
                                 images=all_images, models=models)
    return heuristic_storyboard(protocol, images=all_images, models=models)


def render_video(board: Storyboard, out_path: Path, *, voice: str = "gtts", lang: str = "en",
                 work_dir: Path | None = None, log: Callable[[str], None] = print) -> Path:
    check_ffmpeg()
    out_path = Path(out_path)
    tmp = None
    if work_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="protovid-")
        work_dir = Path(tmp.name)
    work_dir.mkdir(parents=True, exist_ok=True)

    library = _load_library(board, log)
    frame_cache: dict[tuple, Path] = {}

    clips: list[Path] = []
    total = len(board.scenes)
    for i, scene in enumerate(board.scenes):
        log(f"[{i + 1}/{total}] {scene.kind:<9} {scene.heading}")
        photo = board.image_path(scene.image)
        if scene.image and photo is None:
            log(f"  ! image not found, skipping: {scene.image}")
        elif photo is not None:
            log(f"  image: {scene.image}")
        frames_dir = None
        resolved = library.resolve(scene.model_part) if (library and scene.kind not in ("title", "summary")) else None
        if scene.model_part and resolved is None:
            log(f"  ! 3D part not found, skipping: {scene.model_part}")
        if resolved:
            model, parts = resolved
            view = scene.view3d or ("closeup" if parts else "orbit")
            accent = THEMES.get(scene.kind, THEMES["step"])["accent"]
            key = (model.name, tuple(parts), view, accent)
            if key not in frame_cache:
                log(f"  3D: {scene.model_part} ({view}) - rendering...")
                _, _, pw, ph = panel_inner()
                frame_dir = work_dir / f"3d_{len(frame_cache):03d}"
                render_frames(model, parts, view, frame_dir, size=(pw, ph), accent=accent)
                frame_cache[key] = frame_dir
            else:
                log(f"  3D: {scene.model_part} ({view})")
            frames_dir = frame_cache[key]
        slide = render_scene(scene, i, total, board.title, work_dir / f"slide_{i:03d}.png",
                             photo=photo, panel3d=frames_dir is not None)
        try:
            audio = synthesize(scene.narration, work_dir / f"voice_{i:03d}.mp3", backend=voice, lang=lang)
        except Exception as exc:  # network down, TTS quota, missing engine...
            log(f"  ! narration failed ({exc.__class__.__name__}: {exc}); using a silent slide")
            audio = None
        seconds = (audio_duration(audio) if audio else estimate_seconds(scene.narration)) + PAD_SECONDS
        if frames_dir is not None:
            px, py, _, _ = panel_inner()
            fg = render_panel_foreground(THEMES.get(scene.kind, THEMES["step"])["accent"], photo,
                                         work_dir / f"fg_{i:03d}.png")
            clips.append(make_clip(slide, audio, seconds, work_dir / f"clip_{i:03d}.mp4",
                                   anim_frames=frames_dir, anim_xy=(px, py), foreground=fg))
        else:
            clips.append(make_clip(slide, audio, seconds, work_dir / f"clip_{i:03d}.mp4"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat(clips, out_path)
    if tmp is not None:
        tmp.cleanup()
    return out_path


def _load_library(board: Storyboard, log: Callable[[str], None]) -> ModelLibrary | None:
    if not board.models:
        return None
    models = []
    for name, spec in board.models.items():
        try:
            m = load_model(spec["path"], up=spec.get("up"))
            m.name = name
            models.append(m)
        except Exception as exc:
            log(f"  ! could not load 3D model {spec.get('path')}: {exc}")
    return ModelLibrary(models) if models else None
