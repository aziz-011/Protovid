"""User-supplied images (machines, components, tools...) for the slides."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_IMAGES_FOR_CLAUDE = 50   # the API accepts up to 100 images per request
PREVIEW_PX = 768             # downscale before sending: cheaper, plenty to recognise parts


@dataclass
class ImageAsset:
    name: str          # file name, used as the ID in the storyboard
    path: Path
    label: str         # human-readable: caption, or from the file name ("filter_housing.jpg" -> "filter housing")
    source: str = "folder"   # "document" (embedded in the .docx) or "folder" (user-supplied)

    def preview_b64(self) -> str:
        with Image.open(self.path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((PREVIEW_PX, PREVIEW_PX))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _label(stem: str) -> str:
    return re.sub(r"[_\-.]+", " ", stem).strip()


def load_images(folder: str | Path | None) -> list[ImageAsset]:
    if not folder:
        return []
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Image folder not found: {folder}")
    assets = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in IMAGE_EXTS and p.is_file():
            try:
                with Image.open(p) as im:
                    im.verify()
            except Exception:
                print(f"  ! skipping unreadable image: {p.name}")
                continue
            assets.append(ImageAsset(name=p.name, path=p.resolve(), label=_label(p.stem)))
    return assets


def from_figures(figures) -> list[ImageAsset]:
    """Pictures embedded in the Word document (see docx_reader.Figure)."""
    return [ImageAsset(name=f.name, path=f.path, label=f.caption or _label(Path(f.name).stem), source="document")
            for f in figures]


def merge(*groups: list[ImageAsset]) -> list[ImageAsset]:
    """Combine image lists, renaming clashes so every name is unique."""
    out: list[ImageAsset] = []
    names: set[str] = set()
    for group in groups:
        for a in group:
            name, k = a.name, 2
            while name in names:
                name = f"{Path(a.name).stem}_{k}{Path(a.name).suffix}"
                k += 1
            names.add(name)
            out.append(ImageAsset(name=name, path=a.path, label=a.label, source=a.source))
    return out


def claude_image_blocks(assets: list[ImageAsset]) -> list[dict]:
    """Content blocks that show Claude each image, labelled by its file name."""
    blocks: list[dict] = []
    for a in assets[:MAX_IMAGES_FOR_CLAUDE]:
        desc = f"Image file: {a.name}"
        if a.source == "document":
            desc += f" (embedded in the protocol; caption/context: {a.label or 'none'})"
        blocks.append({"type": "text", "text": desc})
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": a.preview_b64()},
        })
    return blocks


_STOP = {"the", "a", "an", "and", "of", "to", "for", "with", "on", "in", "img", "image", "photo", "pic"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def match_by_name(text: str, assets: list[ImageAsset]) -> str | None:
    """Offline matching: pick the image whose file-name words best overlap the text."""
    words = _words(text)
    best, best_score = None, 0.0
    for a in assets:
        label = _words(a.label)
        if not label:
            continue
        hits = sum(1 for w in label if w in words or w.rstrip("s") in words)
        score = hits / len(label)
        # short labels ("tube cutter") must match fully; longer ones mostly
        needed = 1.0 if len(label) <= 2 else 0.6
        if score >= needed and score > best_score:
            best, best_score = a.name, score
    return best
