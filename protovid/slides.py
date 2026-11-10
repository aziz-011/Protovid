"""Render storyboard scenes to 1920x1080 PNG slides with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .storyboard import Scene

W, H = 1920, 1080
MARGIN = 140

THEMES = {
    "title":     {"bg": (14, 42, 71),   "accent": (56, 189, 248), "fg": (255, 255, 255)},
    "overview":  {"bg": (248, 250, 252), "accent": (14, 116, 144), "fg": (15, 23, 42)},
    "materials": {"bg": (248, 250, 252), "accent": (21, 128, 61),  "fg": (15, 23, 42)},
    "step":      {"bg": (248, 250, 252), "accent": (14, 116, 144), "fg": (15, 23, 42)},
    "warning":   {"bg": (254, 243, 199), "accent": (217, 119, 6),  "fg": (69, 26, 3)},
    "summary":   {"bg": (14, 42, 71),   "accent": (56, 189, 248), "fg": (255, 255, 255)},
}

_FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "regular": [
        "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES[weight]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _paste_photo(canvas: Image.Image, photo_path: Path, box: tuple[int, int, int, int],
                 accent: tuple[int, int, int]) -> None:
    """Fit the photo inside ``box`` (keeps aspect ratio), on a white card with an accent border."""
    x0, y0, x1, y1 = box
    with Image.open(photo_path) as ph:
        ph = ImageOps.exif_transpose(ph).convert("RGB")
        ph = ImageOps.contain(ph, (x1 - x0 - 40, y1 - y0 - 40), Image.LANCZOS)
    card_w, card_h = ph.width + 40, ph.height + 40
    cx = x0 + (x1 - x0 - card_w) // 2
    cy = y0 + (y1 - y0 - card_h) // 2
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([cx + 8, cy + 10, cx + card_w + 8, cy + card_h + 10], 24, fill=(0, 0, 0, 40) if canvas.mode == "RGBA" else (210, 214, 220))
    d.rounded_rectangle([cx, cy, cx + card_w, cy + card_h], 24, fill=(255, 255, 255), outline=accent, width=4)
    canvas.paste(ph, (cx + 20, cy + 20))


PANEL_BOX = (int(W * 0.53), 110, W - 90, H - 120)   # right-hand media area
PANEL_PAD = 20


def panel_inner() -> tuple[int, int, int, int]:
    """(x, y, width, height) of the area the 3D animation is overlaid on (even sizes for H.264)."""
    x0, y0, x1, y1 = PANEL_BOX
    w = (x1 - x0 - 2 * PANEL_PAD) // 2 * 2
    h = (y1 - y0 - 2 * PANEL_PAD) // 2 * 2
    return x0 + PANEL_PAD, y0 + PANEL_PAD, w, h


def _draw_3d_panel(canvas: Image.Image, accent, photo: Path | None) -> None:
    """White card where the 3D animation goes (the tag and photo inset go in the foreground layer)."""
    x0, y0, x1, y1 = PANEL_BOX
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([x0 + 8, y0 + 10, x1 + 8, y1 + 10], 24, fill=(210, 214, 220))
    d.rounded_rectangle([x0, y0, x1, y1], 24, fill=(255, 255, 255), outline=accent, width=4)


def render_panel_foreground(accent, photo: Path | None, out: Path) -> Path:
    """Transparent full-frame layer drawn OVER the 3D animation: '3D' tag + real-photo inset."""
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0, x1, y1 = PANEL_BOX
    d = ImageDraw.Draw(canvas)
    f = _font("bold", 26)
    d.rounded_rectangle([x0 + 24, y1 - 64, x0 + 112, y1 - 24], 12, fill=accent)
    d.text((x0 + 68, y1 - 44), "3D", font=f, fill=(255, 255, 255), anchor="mm")
    if photo is not None:  # real photo inset, bottom-right corner (drawn over by nothing)
        with Image.open(photo) as ph:
            ph = ImageOps.exif_transpose(ph).convert("RGB")
            ph = ImageOps.contain(ph, (260, 220), Image.LANCZOS)
        px, py = x1 - ph.width - 30, y1 - ph.height - 30
        d.rounded_rectangle([px - 8, py - 8, px + ph.width + 8, py + ph.height + 8], 12,
                            fill=(255, 255, 255), outline=accent, width=3)
        canvas.paste(ph, (px, py))
    canvas.save(out)
    return out


def render_scene(scene: Scene, index: int, total: int, doc_title: str, out: Path,
                 photo: Path | None = None, panel3d: bool = False) -> Path:
    theme = THEMES.get(scene.kind, THEMES["step"])
    img = Image.new("RGB", (W, H), theme["bg"])
    d = ImageDraw.Draw(img)
    width = W - 2 * MARGIN
    if panel3d and scene.kind not in ("title", "summary"):
        width = int(W * 0.50) - MARGIN
        _draw_3d_panel(img, theme["accent"], photo)
    elif photo is not None and scene.kind not in ("title", "summary"):
        # Text column on the left, photo on the right
        width = int(W * 0.50) - MARGIN
        _paste_photo(img, photo, (int(W * 0.53), 110, W - 90, H - 120), theme["accent"])

    if scene.kind in ("title", "summary"):
        d.rectangle([0, H - 24, W * (index + 1) // total, H], fill=theme["accent"])
        if photo is not None:
            _paste_photo(img, photo, (W // 2 - 360, H - 470, W // 2 + 360, H - 60), theme["accent"])
            d = ImageDraw.Draw(img)
        f_big = _font("bold", 96)
        lines = _wrap(d, scene.heading, f_big, width)
        y = H // 2 - len(lines) * 60 - (40 if scene.bullets else 0)
        if photo is not None:
            y = 130
        for ln in lines:
            d.text((W // 2, y), ln, font=f_big, fill=theme["fg"], anchor="mt")
            y += 120
        d.rectangle([W // 2 - 80, y + 10, W // 2 + 80, y + 18], fill=theme["accent"])
        f_sub = _font("regular", 44)
        y += 60
        for b in scene.bullets[:4]:
            for ln in _wrap(d, b, f_sub, width):
                d.text((W // 2, y), ln, font=f_sub, fill=theme["fg"], anchor="mt")
                y += 60
        img.save(out)
        return out

    # Header band
    d.rectangle([0, 0, 24, H], fill=theme["accent"])
    f_tag = _font("bold", 36)
    tag = {
        "step": f"STEP {scene.step_number}" if scene.step_number else "STEP",
        "warning": "⚠  CAUTION" if _font("bold", 36).getbbox("⚠")[2] > 0 else "CAUTION",
        "materials": "YOU WILL NEED",
        "overview": "OVERVIEW",
    }.get(scene.kind, scene.kind.upper())
    d.text((MARGIN, 110), tag, font=f_tag, fill=theme["accent"])

    f_head = _font("bold", 76)
    y = 170
    for ln in _wrap(d, scene.heading, f_head, width)[:2]:
        d.text((MARGIN, y), ln, font=f_head, fill=theme["fg"])
        y += 92
    y += 40

    f_bul = _font("regular", 50)
    for b in scene.bullets[:5]:
        lines = _wrap(d, b, f_bul, width - 70)
        d.ellipse([MARGIN, y + 20, MARGIN + 22, y + 42], fill=theme["accent"])
        for ln in lines:
            d.text((MARGIN + 60, y), ln, font=f_bul, fill=theme["fg"])
            y += 66
        y += 26
        if y > H - 180:
            break

    # Footer: doc title + progress
    f_foot = _font("regular", 28)
    d.text((MARGIN, H - 80), doc_title, font=f_foot, fill=(100, 116, 139))
    d.text((W - MARGIN, H - 80), f"{index + 1} / {total}", font=f_foot, fill=(100, 116, 139), anchor="ra")
    d.rectangle([0, H - 12, W * (index + 1) // total, H], fill=theme["accent"])

    img.save(out)
    return out
