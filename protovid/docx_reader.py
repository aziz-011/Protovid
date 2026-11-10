"""Read a Word protocol into plain, structure-preserving text plus its pictures.

* Tracked changes are read as if accepted: inserted text is kept, deleted text
  is dropped.
* Pictures embedded in the body (figures, photos in tables) are saved to a
  folder and marked in the text as ``[IMAGE name]`` where they appear, so the
  script writer knows which step each picture belongs to.
* Document-control front matter (approval / revision tables, table of
  contents) before the first heading is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.document import Document as _Document

W_P, W_TBL, W_T, W_TAB, W_BR = qn("w:p"), qn("w:tbl"), qn("w:t"), qn("w:tab"), qn("w:br")
W_DEL, W_MOVE_FROM = qn("w:del"), qn("w:moveFrom")
A_BLIP = qn("a:blip")
R_EMBED = qn("r:embed")
MIN_FIGURE_PX = 48  # skip bullets, icons, spacer images


@dataclass
class Figure:
    name: str          # file name inside ProtocolDoc.figures_dir
    path: Path
    caption: str       # caption or the table-cell text next to it ("" if none)


@dataclass
class ProtocolDoc:
    title: str
    text: str  # Markdown-ish text that keeps headings, lists, tables and [IMAGE ...] markers
    headings: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #

def _deleted(el) -> bool:
    """True if ``el`` sits inside a tracked deletion."""
    parent = el.getparent()
    while parent is not None:
        if parent.tag in (W_DEL, W_MOVE_FROM):
            return True
        parent = parent.getparent()
    return False


def _accepted_text(el) -> str:
    """Text of a paragraph/cell with all tracked changes accepted."""
    out: list[str] = []
    for node in el.iter(W_T, W_TAB, W_BR):
        if _deleted(node):
            continue
        if node.tag == W_T:
            out.append(node.text or "")
        elif node.tag == W_TAB:
            out.append("\t")
        else:
            out.append(" ")
    return re.sub(r"[  ]+", " ", "".join(out)).strip()


def _blips(el) -> list[str]:
    return [b.get(R_EMBED) for b in el.iter(A_BLIP) if b.get(R_EMBED) and not _deleted(b)]


def _style_name(document: _Document, p_el) -> str:
    ppr = p_el.pPr
    style_id = ppr.pStyle.val if ppr is not None and ppr.pStyle is not None else None
    if not style_id:
        return "normal"
    try:
        return (document.styles.get_by_id(style_id, docx.enum.style.WD_STYLE_TYPE.PARAGRAPH).name or "").lower()
    except Exception:
        return style_id.lower()


def _is_list(p_el) -> bool:
    ppr = p_el.pPr
    return ppr is not None and ppr.numPr is not None


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #

class _FigureSaver:
    def __init__(self, document: _Document, out_dir: Path | None):
        self.document = document
        self.out_dir = out_dir
        self.figures: list[Figure] = []
        self._by_rid: dict[str, Figure] = {}

    def save(self, rid: str, caption: str = "") -> Figure | None:
        if self.out_dir is None:
            return None
        if rid in self._by_rid:
            return self._by_rid[rid]
        try:
            part = self.document.part.related_parts[rid]
            blob = part.blob
        except (KeyError, AttributeError):
            return None
        ext = Path(str(part.partname)).suffix.lower() or ".png"
        if ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}:
            return None  # EMF/WMF vector clip-art etc.
        try:
            from io import BytesIO
            from PIL import Image

            with Image.open(BytesIO(blob)) as im:
                if max(im.size) < MIN_FIGURE_PX:
                    return None
        except Exception:
            return None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = f"doc_fig{len(self.figures) + 1:02d}{ext}"
        path = (self.out_dir / name).resolve()
        path.write_bytes(blob)
        fig = Figure(name=name, path=path, caption=caption)
        self.figures.append(fig)
        self._by_rid[rid] = fig
        return fig


def read_protocol(path: str | Path, figures_dir: str | Path | None = None) -> ProtocolDoc:
    """Read ``path``. If ``figures_dir`` is given, embedded pictures are saved there."""
    path = Path(path)
    if path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file, got: {path.name}")
    document = docx.Document(str(path))
    saver = _FigureSaver(document, Path(figures_dir) if figures_dir else None)

    lines: list[str] = []
    headings: list[str] = []
    title = ""
    seen_heading = False
    pending_caption_for: list[Figure] = []  # figures waiting for the caption paragraph after them

    blocks = list(document.element.body.iterchildren())
    for block in blocks:
        if block.tag == W_TBL:
            if not seen_heading:
                continue  # approval / revision / classification tables
            for row in block.iter(qn("w:tr")):
                cells: list[str] = []
                row_figs: list[Figure] = []
                for tc in row.iter(qn("w:tc")):
                    text = _accepted_text(tc)
                    for rid in _blips(tc):
                        fig = saver.save(rid, caption=text)
                        if fig:
                            row_figs.append(fig)
                            text = f"{text} [IMAGE {fig.name}]".strip()
                    if not cells or text != cells[-1]:  # merged cells repeat their text
                        cells.append(text)
                row_text = " – ".join(c for c in cells if c and "[IMAGE" not in c)
                for fig in row_figs:
                    fig.caption = fig.caption or row_text
                if any(c for c in cells):
                    lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            continue

        if block.tag != W_P:
            continue
        style = _style_name(document, block)
        text = _accepted_text(block)
        rids = _blips(block)

        if style.startswith("toc") or (not seen_heading and re.fullmatch(r"(summary|contents|table of contents|sommaire)", text, re.I)):
            continue

        if style == "caption" and pending_caption_for:
            for fig in pending_caption_for:
                fig.caption = fig.caption or text
            pending_caption_for = []
            lines.append(f"({text})")
            continue

        for rid in rids:
            fig = saver.save(rid)
            if fig:
                lines.append(f"[IMAGE {fig.name}]")
                pending_caption_for.append(fig)
        if not text:
            continue
        if rids == [] and pending_caption_for and style != "caption":
            pending_caption_for = []

        if style == "title":
            title = title or text
            lines.append(f"# {text}")
        elif style.startswith("heading"):
            seen_heading = True
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append(f"{'#' * (int(level) + 1)} {text}")
            headings.append(text)
        elif not seen_heading:
            if not title and len(text) < 150:
                title = text
                lines.append(f"# {text}")
        elif "list number" in style:
            lines.append(f"1. {text}")
        elif "list" in style or _is_list(block):
            lines.append(f"- {text}")
        else:
            lines.append(text)

    core_title = (document.core_properties.title or "").strip()
    if not title:
        if core_title and not re.search(r"template|mod[eè]le|document", core_title, re.I):
            title = core_title
        else:
            title = headings[0] if headings else path.stem.replace("_", " ").title()

    # Number "1." list items in order within each run of consecutive items.
    numbered, n = [], 0
    for ln in lines:
        if ln.startswith("1. "):
            n += 1
            numbered.append(f"{n}. {ln[3:]}")
        else:
            if not ln.startswith(("[IMAGE", "(")):
                n = 0 if ln.startswith("#") else n
            numbered.append(ln)

    return ProtocolDoc(
        title=title,
        text="\n".join(numbered).strip(),
        headings=headings,
        figures=saver.figures,
    )
