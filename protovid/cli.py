"""Command line: ``protovid my_protocol.docx -o my_protocol.mp4``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import build_storyboard, render_video
from .images import load_images
from .storyboard import Storyboard, StoryboardError


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="protovid", description="Turn a Word protocol into a narrated video.")
    p.add_argument("input", type=Path, help="Protocol .docx file (or a storyboard .json from --script-only)")
    p.add_argument("-o", "--output", type=Path, help="Output .mp4 (default: next to the input)")
    p.add_argument("-i", "--images", type=Path,
                   help="Folder of photos (machines, components, tools...) to show on the slides")
    p.add_argument("--no-ai", action="store_true", help="Build the script offline from headings/lists (no Claude)")
    p.add_argument("--voice", choices=["gtts", "espeak", "none"], default="gtts", help="Narration engine")
    p.add_argument("--lang", default="en", help="Narration language code for TTS, e.g. en, fr, es")
    p.add_argument("--language", help="Ask Claude to write the script in this language, e.g. French")
    p.add_argument("--audience", help="Who the video is for, e.g. 'new field technicians'")
    p.add_argument("--script-only", action="store_true", help="Only write the storyboard JSON so you can edit it")
    p.add_argument("--keep-frames", type=Path, help="Keep slides/audio/clips in this folder")
    args = p.parse_args(argv)

    if not args.input.exists():
        p.error(f"File not found: {args.input}")

    images = load_images(args.images) if args.images else []
    if args.images:
        print(f"Images: {len(images)} found in {args.images}")

    try:
        if args.input.suffix.lower() == ".json":
            board = Storyboard.from_json(args.input.read_text(encoding="utf-8"))
            if images:  # let the user point an edited storyboard at a (new) image folder
                board.images_dir = str(images[0].path.parent)
        else:
            print(f"Reading {args.input.name} and writing the script"
                  f"{' with Claude' if not args.no_ai else ''}...")
            board = build_storyboard(args.input, use_ai=not args.no_ai,
                                     language=args.language, audience=args.audience,
                                     images=images)
    except StoryboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    script_path = args.input.with_suffix(".storyboard.json")
    if args.input.suffix.lower() != ".json":
        script_path.write_text(board.to_json(), encoding="utf-8")
        print(f"Storyboard: {script_path} ({len(board.scenes)} scenes)")
    if args.script_only:
        return 0

    out = args.output or args.input.with_suffix(".mp4")
    if out.name.endswith(".storyboard.mp4"):
        out = out.with_name(out.name.replace(".storyboard.mp4", ".mp4"))
    render_video(board, out, voice=args.voice, lang=args.lang, work_dir=args.keep_frames)
    print(f"Video: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
