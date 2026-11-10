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
| `-m model.step` | 3D CAD model (STEP, GLB, OBJ, STL); repeat `-m` for several |
| `-i photos/` | Folder of your photos (machines, components, tools) to show on the slides |
| `--audience "new field technicians"` | Pitch the script at a given audience |
| `--context "The machine shown is the Kaia water fountain"` | Extra instructions for the script (machine, site, wording...) |
| `--language French --lang fr` | Script in French, French voice |
| `--script-only` | Only write the storyboard JSON so you can review/edit it |
| `protovid my_protocol.storyboard.json` | Render a video from an (edited) storyboard |
| `--voice gtts \| espeak \| none` | Voice engine: Google TTS (default, needs internet), offline espeak-ng, or silent |
| `--no-doc-images` | Don't use the pictures embedded in the Word file |
| `--no-ai` | Build the script from the document's headings and lists, without Claude |
| `--keep-frames DIR` | Keep the slide PNGs, audio and clips |

### 3D views from your CAD models

Give Protovid your CAD assembly and the video shows the machine in 3D, with
the part each step is about highlighted and the rest of the machine faded:

```bash
pip install -e ".[3d]"
protovid procedure.docx -m dosing_pump_assembly.step -m bag_support_A.step
```

- **Formats:** STEP/STP (straight from SolidWorks, Fusion, Inventor, FreeCAD...),
  GLB/glTF, OBJ, STL. STEP or GLB keep the part names from the CAD tree, and
  **those names are what Claude uses to pick the part for each step**. Keep them
  meaningful (`Rotor`, `Lower lid`, `Mineralisation tube`, not `Part37`).
  STL/OBJ exports are usually one single part, so they only allow whole-model views.
- **Views Claude can choose per slide:**
  - `closeup`: zooms onto the part (e.g. the rotor to turn, the lid to open) and rocks gently.
  - `exploded`: parts move apart and back together; good for mounting/assembly steps.
  - `orbit`: the whole assembly turns, optionally with one part highlighted.
- If the slide also has a photo, the real photo is shown as a small inset next
  to the model, so the viewer links the model to the real machine.
- Copies of a part (`Screw M4:1`, `Screw M4:2`...) are grouped and highlighted together.
- If a model appears lying on its side, set the up axis: `--model-up z` (or `y`).
- To change a choice, edit `"model_part"` / `"view3d"` in the storyboard JSON
  (pass `-m` again when rendering an edited storyboard if the model moved).
- Rendering uses your graphics card through VTK. On a headless Linux server,
  install OSMesa first (`sudo apt install libosmesa6`).

Try it with the demo model:

```bash
python examples/make_sample_model.py
protovid examples/sample_protocol.docx -m examples/models/dosing_pump_demo.glb
```

### Pictures already in the Word file

Pictures embedded in the protocol (figures, photos in the tools table) are
extracted automatically to `<protocol>_images/` and placed on the slide of the
step they belong to, using their position and caption. Use `--no-doc-images`
to ignore them. Tracked changes are read as if accepted, and the front
matter (approval/revision tables, table of contents) is skipped.

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
| `protovid/render3d.py` | Loads CAD models (parts by name) and renders the 3D orbit / close-up / exploded animations |
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
