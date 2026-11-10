"""Turn protocol text into a storyboard (list of scenes).

Two modes:
  * ``claude_storyboard`` - Claude reads the protocol and writes the script.
  * ``heuristic_storyboard`` - offline fallback that splits on headings/lists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict

from .docx_reader import ProtocolDoc
from .images import ImageAsset, claude_image_blocks, match_by_name

MODEL = "claude-opus-5-5"

SCENE_KINDS = ["title", "overview", "materials", "step", "warning", "summary"]


@dataclass
class Scene:
    kind: str
    heading: str
    bullets: list[str] = field(default_factory=list)
    narration: str = ""
    step_number: int | None = None
    image: str | None = None  # a key of Storyboard.images


@dataclass
class Storyboard:
    title: str
    scenes: list[Scene]
    images: dict[str, str] = field(default_factory=dict)  # image name -> file path

    def image_path(self, name: str | None) -> Path | None:
        if not name or name not in self.images:
            return None
        p = Path(self.images[name])
        return p if p.is_file() else None

    def to_json(self) -> str:
        return json.dumps(
            {"title": self.title, "images": self.images,
             "scenes": [asdict(s) for s in self.scenes]},
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Storyboard":
        data = json.loads(raw)
        scenes = [
            Scene(
                kind=s.get("kind", "step") if s.get("kind") in SCENE_KINDS else "step",
                heading=s.get("heading", "").strip(),
                bullets=[b.strip() for b in s.get("bullets", []) if b.strip()],
                narration=s.get("narration", "").strip(),
                step_number=s.get("step_number"),
                image=s.get("image") or None,
            )
            for s in data["scenes"]
        ]
        images = dict(data.get("images") or {})
        if data.get("images_dir"):  # older storyboard format: one folder
            for sc in scenes:
                if sc.image and sc.image not in images:
                    images[sc.image] = str(Path(data["images_dir"]) / sc.image)
        return cls(title=data["title"], scenes=scenes, images=images)


# --------------------------------------------------------------------------- #
# Claude-powered storyboard
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """\
You turn written protocols (standard operating procedures, lab, maintenance, \
safety or training protocols) into the script for a short instructional video.

The video is a sequence of slides. Each slide shows a heading and a few short \
on-screen bullets while a narrator reads the narration aloud.

How to write it:
- Start with one "title" scene, then an "overview" scene saying what the \
protocol is for and who it is for.
- If the protocol lists equipment, materials, reagents or PPE, add a \
"materials" scene.
- Give each procedural step its own "step" scene, numbered in order with \
step_number. Merge trivial sub-steps; split steps that do several things.
- Put every safety caution, hazard or critical check from the source in a \
"warning" scene right before the step it applies to, so the viewer hears it \
in time.
- End with a "summary" scene recapping the key points.
- Bullets are what the viewer reads: at most 4 per slide, each under 12 words, \
imperative voice, with exact quantities, settings and times from the source.
- Narration is spoken: one to three plain, natural sentences per scene \
(roughly 15-60 words). No markdown, no bullet symbols, spell out units the \
way a person would say them.
- Stay faithful to the source. Never invent quantities, values, timings or \
safety claims that are not in the protocol. If something is ambiguous, keep \
the source wording.
- Write in the same language as the protocol unless told otherwise.
- Skip document-control material (approval and revision tables, \
classification, table of contents) and blank forms or record sheets; one \
mention that the record sheet must be completed is enough.
- Tables of steps, materials, acceptance criteria or troubleshooting are \
content: use them.
"""

IMAGES_PROMPT = """
Photos of the equipment are attached, each preceded by its file name. Pictures embedded in the protocol also appear in the protocol text as [IMAGE file-name] markers where they belong, usually followed by their caption; prefer them for the step they sit next to. For every scene, set "image" to the file name of the photo that best shows the machine, component, tool or material the scene talks about, so the viewer can recognise it. Look at what each photo actually shows; use the file name as a hint. Reuse a photo across scenes when it fits. Use null when no photo clearly matches - a wrong picture is worse than none. When a photo shows a part the protocol names, you may use the name visible in the photo in the bullets, but do not describe details you cannot see.
"""

def storyboard_schema(image_names: list[str]) -> dict:
    image_field = (
        {"anyOf": [{"type": "string", "enum": image_names}, {"type": "null"}]}
        if image_names else {"type": "null"}
    )
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": SCENE_KINDS},
                        "heading": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                        "narration": {"type": "string"},
                        "step_number": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "image": image_field,
                    },
                    "required": ["kind", "heading", "bullets", "narration", "step_number", "image"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "scenes"],
        "additionalProperties": False,
    }


class StoryboardError(RuntimeError):
    pass


def claude_storyboard(
    protocol: ProtocolDoc,
    *,
    language: str | None = None,
    audience: str | None = None,
    model: str = MODEL,
    images: list[ImageAsset] | None = None,
) -> Storyboard:
    import anthropic

    client = anthropic.Anthropic()

    extra = []
    if language:
        extra.append(f"Write the bullets and narration in {language}.")
    if audience:
        extra.append(f"The audience is: {audience}.")

    user_content = (
        f"<protocol title=\"{protocol.title}\">\n{protocol.text}\n</protocol>\n\n"
        "Write the video storyboard for this protocol."
        + ("\n" + "\n".join(extra) if extra else "")
    )
    images = images or []
    image_blocks = claude_image_blocks(images)
    usable = [b["text"].removeprefix("Image file: ") for b in image_blocks if b["type"] == "text"]
    content = image_blocks + [{"type": "text", "text": user_content}]

    with client.beta.messages.stream(
        model=model,
        max_tokens=64000,
        system=SYSTEM_PROMPT + (IMAGES_PROMPT if usable else ""),
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": storyboard_schema(usable)},
        },
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise StoryboardError("Claude declined to write a storyboard for this document.")
    if message.stop_reason == "max_tokens":
        raise StoryboardError("The storyboard was cut off (max_tokens). Try a shorter protocol.")

    raw = "".join(b.text for b in message.content if b.type == "text")
    try:
        board = Storyboard.from_json(raw)
    except (json.JSONDecodeError, KeyError) as exc:
        raise StoryboardError(f"Could not parse storyboard JSON: {exc}") from exc
    if not board.scenes:
        raise StoryboardError("Claude returned an empty storyboard.")
    for scene in board.scenes:
        if scene.image not in usable:
            scene.image = None
    board.images = {a.name: str(a.path) for a in images}
    return board


# --------------------------------------------------------------------------- #
# Offline heuristic storyboard (no API key needed)
# --------------------------------------------------------------------------- #

_WARN = re.compile(r"\b(warning|caution|danger|hazard|do not|never|attention|avertissement|attention)\b", re.I)
_SKIP_SECTION = re.compile(
    r"\b(appendix|annex|annexe|terminology|abbreviations?|glossary|applicable documents|"
    r"general information|conductivity reading|result and sign-off|sign-off|record sheet)\b", re.I)
_SAFETY = re.compile(r"\b(safety|ppe|sécurité|epi)\b", re.I)
_MATERIALS = re.compile(r"\b(materials?|equipment|reagents?|supplies|tools|ppe|matériel)\b", re.I)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def heuristic_storyboard(protocol: ProtocolDoc, images: list[ImageAsset] | None = None) -> Storyboard:
    images = images or []
    scenes: list[Scene] = [
        Scene(kind="title", heading=protocol.title, narration=f"{_sentence(protocol.title)} A step by step guide.")
    ]
    marker = re.compile(r"\s*\[IMAGE ([^\]]+)\]")
    section: str | None = None
    items: list[tuple[str, str | None, bool]] = []   # (text, image placed right after it, numbered?)
    skipping = False
    paragraphs: list[str] = []
    section_image: str | None = None
    step_no = 0

    def flush():
        nonlocal step_no, items, paragraphs, section_image, skipping
        heading = section or "Overview"
        if section and (_SKIP_SECTION.search(section) or skipping):
            items, paragraphs, section_image = [], [], None
            return
        is_safety = bool(section and _SAFETY.search(section))
        is_materials = bool(section and _MATERIALS.search(section)) and not is_safety
        numbered = [(t, i) for t, i, n in items if n]
        bullets = [(t, i) for t, i, n in items if not n]
        if is_safety and bullets:
            texts = [t for t, _ in bullets]
            scenes.append(Scene("warning", heading, texts[:4], " ".join(_sentence(t) for t in texts)))
            bullets = []
        if numbered and not is_materials:
            for text, img in numbered:
                if _WARN.search(text):
                    scenes.append(Scene("warning", "Caution", [text], _sentence(text), image=img))
                    continue
                step_no += 1
                scenes.append(Scene("step", heading, [text], _sentence(text), step_no, image=img))
        if bullets and not is_materials:
            for k in range(0, len(bullets), 4):
                chunk = bullets[k:k + 4]
                texts = [t for t, _ in chunk]
                kind = "warning" if any(_WARN.search(t) for t in texts) else "overview"
                img = next((i for _, i in chunk if i), section_image)
                scenes.append(Scene(kind, heading, texts, " ".join(_sentence(t) for t in texts), image=img))
            items = []
        if is_materials and (items or paragraphs):
            names = [t for t, _, _ in items] or [
                p.split(" – ")[1] if p.count(" – ") >= 2 else p for p in paragraphs[1:] or paragraphs]
            scenes.append(Scene("materials", heading, names[:6], "You will need: " + ", ".join(names) + ".",
                                image=section_image))
            paragraphs = []
        if paragraphs and len(paragraphs) > 6:
            paragraphs = paragraphs[:1]  # long tables: keep the intro sentence only
        if paragraphs:
            body = " ".join(paragraphs)
            kind = "warning" if _WARN.search(body) else "overview"
            scenes.append(Scene(kind, heading, [p[:90] for p in paragraphs[:3]], body, image=section_image))
        items, paragraphs, section_image = [], [], None

    for line in protocol.text.splitlines():
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        only = marker.fullmatch(line)
        if only:  # picture on its own line: belongs to the item/paragraph above it
            if items and items[-1][1] is None:
                items[-1] = (items[-1][0], only.group(1), items[-1][2])
            else:
                section_image = section_image or only.group(1)
            continue
        if line.startswith("(") and line.endswith(")"):
            continue  # figure caption
        inline = marker.findall(line)
        if inline:
            section_image = section_image or inline[0]
        line = marker.sub("", line).strip()
        if line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            section = line.lstrip("#").strip()
            if level <= 2:  # top-level section: an appendix skips all its sub-sections
                skipping = bool(re.search(r"\b(appendix|annex|annexe)\b", section, re.I))
        elif re.match(r"^(- |\d+\. )", line):
            items.append((line.split(" ", 1)[1], None, not line.startswith("- ")))
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip() and c.strip() != "☐"]
            if cells:
                paragraphs.append(" – ".join(cells))
        else:
            paragraphs.append(line)
    flush()

    scenes.append(
        Scene("summary", "Summary", [f"{step_no} steps completed"] if step_no else [],
              f"That completes the {protocol.title} protocol.")
    )
    board = Storyboard(title=protocol.title, scenes=scenes, images={a.name: str(a.path) for a in images})
    for scene in scenes:
        if scene.kind != "title" and not scene.image and images:
            scene.image = match_by_name(" ".join([scene.heading, *scene.bullets]), images)
    return board
