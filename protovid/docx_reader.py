"""Read a Word protocol into plain, structure-preserving text."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph


@dataclass
class ProtocolDoc:
    title: str
    text: str  # Markdown-ish text that keeps headings, lists and tables
    headings: list[str] = field(default_factory=list)


def _iter_blocks(document: _Document):
    """Yield paragraphs and tables in document order."""
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def read_protocol(path: str | Path) -> ProtocolDoc:
    path = Path(path)
    if path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file, got: {path.name}")
    document = docx.Document(str(path))

    lines: list[str] = []
    headings: list[str] = []
    title = (document.core_properties.title or "").strip()

    for block in _iter_blocks(document):
        if isinstance(block, Table):
            for row in block.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
            continue

        text = block.text.strip()
        if not text:
            continue
        style = (block.style.name if block.style is not None else "").lower()
        if style == "title":
            title = title or text
            lines.append(f"# {text}")
        elif style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append(f"{'#' * (int(level) + 1)} {text}")
            headings.append(text)
        elif "list number" in style:
            lines.append(f"1. {text}")
        elif "list" in style or block._p.pPr is not None and block._p.pPr.numPr is not None:
            lines.append(f"- {text}")
        else:
            lines.append(text)

    if not title:
        title = headings[0] if headings else path.stem.replace("_", " ").title()

    return ProtocolDoc(title=title, text="\n".join(lines).strip(), headings=headings)
