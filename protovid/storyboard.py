"""Turn protocol text into a storyboard (list of scenes).

Two modes:
  * ``claude_storyboard`` - Claude reads the protocol and writes the script.
  * ``heuristic_storyboard`` - offline fallback that splits on headings/lists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from .docx_reader import ProtocolDoc

MODEL = "claude-opus-5-5"

SCENE_KINDS = ["title", "overview", "materials", "step", "warning", "summary"]


@dataclass
class Scene:
    kind: str
    heading: str
    bullets: list[str] = field(default_factory=list)
    narration: str = ""
    step_number: int | None = None


@dataclass
class Storyboard:
    title: str
    scenes: list[Scene]

    def to_json(self) -> str:
        return json.dumps(
            {"title": self.title, "scenes": [asdict(s) for s in self.scenes]},
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
            )
            for s in data["scenes"]
        ]
        return cls(title=data["title"], scenes=scenes)


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
"""

STORYBOARD_SCHEMA = {
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
                },
                "required": ["kind", "heading", "bullets", "narration", "step_number"],
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

    with client.beta.messages.stream(
        model=model,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": STORYBOARD_SCHEMA},
        },
        messages=[{"role": "user", "content": user_content}],
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
    return board


# --------------------------------------------------------------------------- #
# Offline heuristic storyboard (no API key needed)
# --------------------------------------------------------------------------- #

_WARN = re.compile(r"\b(warning|caution|danger|hazard|do not|never|attention|avertissement|attention)\b", re.I)
_MATERIALS = re.compile(r"\b(materials?|equipment|reagents?|supplies|tools|ppe|matériel)\b", re.I)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def heuristic_storyboard(protocol: ProtocolDoc) -> Storyboard:
    scenes: list[Scene] = [
        Scene(kind="title", heading=protocol.title, narration=f"{_sentence(protocol.title)} A step by step guide.")
    ]
    section: str | None = None
    items: list[str] = []
    paragraphs: list[str] = []
    step_no = 0

    def flush():
        nonlocal step_no, items, paragraphs
        if section is None and not items and not paragraphs:
            return
        heading = section or "Overview"
        if items and not (section and _MATERIALS.search(section)):
            for item in items:
                if _WARN.search(item):
                    scenes.append(Scene("warning", "Caution", [item], _sentence(item)))
                    continue
                step_no += 1
                scenes.append(Scene("step", heading, [item], _sentence(item), step_no))
        elif items:
            scenes.append(Scene("materials", heading, items[:6], "You will need: " + ", ".join(items) + "."))
        if paragraphs:
            body = " ".join(paragraphs)
            kind = "warning" if _WARN.search(body) else ("overview" if not items else "step")
            if kind == "step":
                kind = "overview"
            scenes.append(Scene(kind, heading, [p[:90] for p in paragraphs[:3]], body))
        items, paragraphs = [], []

    for line in protocol.text.splitlines():
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("#"):
            flush()
            section = line.lstrip("#").strip()
        elif line.startswith(("- ", "1. ")):
            items.append(line.split(" ", 1)[1])
        elif line.startswith("|"):
            paragraphs.append(" ".join(c.strip() for c in line.strip("|").split("|") if c.strip()))
        else:
            paragraphs.append(line)
    flush()

    scenes.append(
        Scene("summary", "Summary", [f"{step_no} steps completed"] if step_no else [],
              f"That completes the {protocol.title} protocol.")
    )
    return Storyboard(title=protocol.title, scenes=scenes)
