"""Render storyboard scenes to 1920x1080 PNG slides with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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


def render_scene(scene: Scene, index: int, total: int, doc_title: str, out: Path) -> Path:
    theme = THEMES.get(scene.kind, THEMES["step"])
    img = Image.new("RGB", (W, H), theme["bg"])
    d = ImageDraw.Draw(img)
    width = W - 2 * MARGIN

    if scene.kind in ("title", "summary"):
        d.rectangle([0, H - 24, W * (index + 1) // total, H], fill=theme["accent"])
        f_big = _font("bold", 96)
        lines = _wrap(d, scene.heading, f_big, width)
        y = H // 2 - len(lines) * 60 - (40 if scene.bullets else 0)
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
