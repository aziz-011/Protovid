# Protovid

**Give it a Word protocol, get back a narrated step-by-step video.**

```
protocol.docx ──► read headings, lists, tables
              ──► Claude writes the video script (storyboard)
              ──► one slide per scene (title, materials, cautions, numbered steps, summary)
              ──► voice-over for each slide
              ──► protocol.mp4 (1080p, H.264 + AAC)
```

## Install

You need Python 3.10+ and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
pip install -e .
```

Set your Anthropic API key (from https://platform.claude.com):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Use

```bash
protovid my_protocol.docx
```

This writes `my_protocol.storyboard.json` (the script) and `my_protocol.mp4`.

Useful options:

| Option | What it does |
|---|---|
| `-o out.mp4` | Choose the output file |
| `-i photos/` | Folder of your photos (machines, components, tools) to show on the slides |
| `--audience "new field technicians"` | Pitch the script at a given audience |
| `--language French --lang fr` | Script in French, French voice |
| `--script-only` | Only write the storyboard JSON so you can review/edit it |
| `protovid my_protocol.storyboard.json` | Render a video from an (edited) storyboard |
| `--voice gtts \| espeak \| none` | Voice engine: Google TTS (default, needs internet), offline espeak-ng, or silent |
| `--no-ai` | Build the script from the document's headings and lists, without Claude |
| `--keep-frames DIR` | Keep the slide PNGs, audio and clips |

### Adding your own photos

Put your photos of machines, components, tools and materials in one folder
and pass it with `-i`:

```bash
protovid pump_maintenance.docx -i photos/
```

```
photos/
  pump_overview.jpg
  inlet_valve.jpg
  pressure_gauge.png
  filter_cartridge.jpg
```

- **With Claude (default):** Claude looks at every photo and picks the right
  one for each slide based on what it shows. It uses the file name as a hint
  and leaves a slide without a photo when nothing clearly matches.
- **With `--no-ai`:** photos are matched to slides by their file names, so
  name them after the parts in the protocol (`inlet_valve.jpg`, not `IMG_0412.jpg`).
- Slides with a photo put the text on the left and the photo on the right.
  JPG, PNG, WEBP, BMP, GIF and TIFF all work, and phone photos are rotated
  the right way automatically.
- **To fix a choice by hand,** open `*.storyboard.json`, change the `"image"`
  of any scene to another file name (or `null`), and run
  `protovid your.storyboard.json`. Add `-i other_folder/` to use a different folder.

Claude is sent a reduced copy of each photo (max 768 px) and sees up to 50 photos per run.

### Recommended workflow for safety-critical protocols

1. `protovid protocol.docx --script-only`
2. Have someone who knows the protocol check `protocol.storyboard.json`.
3. `protovid protocol.storyboard.json`

Claude is told to stick to the source and never invent quantities or safety
claims, but a human should still sign off before a procedure video is used for training.

## Try it

```bash
python examples/make_sample.py
protovid examples/sample_protocol.docx -i examples/images
```

## How it works

| File | Role |
|---|---|
| `protovid/images.py` | Loads your photo folder, prepares the copies sent to Claude, matches by file name offline |
| `protovid/docx_reader.py` | Reads the `.docx` in order: title, headings, bullet and numbered lists, tables |
| `protovid/storyboard.py` | Claude (`claude-opus-5-5`, structured JSON output) turns the text into scenes; offline heuristic fallback |
| `protovid/slides.py` | Draws 1920×1080 slides with Pillow, color-coded per scene type |
| `protovid/voice.py` | Text-to-speech narration |
| `protovid/video.py` | Builds one clip per slide, timed to its narration, and joins them with ffmpeg |
| `protovid/agent.py` | Runs the whole pipeline |
| `protovid/cli.py` | Command-line interface |

The Claude request has server-side refusal fallback turned on
(`fallbacks: "default"`), so if the main model turns a request down, another model retries it automatically.
