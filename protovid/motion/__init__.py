"""Motion-graphics renderer: each scene is an animated HTML/SVG page recorded frame by frame.

The page (``template.html``) knows a handful of layouts (title, items, warnings,
checklist, machine, diagram, media). Python loads a scene into headless
Chromium, steps the animation clock frame by frame (deterministic, no
real-time recording) and pipes the screenshots into ffmpeg.
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
FPS = 25
W, H = 1920, 1080
LAYOUTS = ["title", "items", "warnings", "checklist", "machine", "diagram", "media"]
ICON_NAMES = [
    "bottle", "sterile_bottle", "spray", "wipes", "gloves", "marker", "coolbox", "clipboard",
    "thermometer", "clock", "tap", "cup", "syringe", "tube", "meter", "key", "phone", "bucket",
    "filter", "hands", "power", "label", "drop", "box",
]


def _chromium_path() -> str | None:
    """Use a pre-installed Chromium if there is one (otherwise Playwright's own)."""
    env = os.environ.get("PROTOVID_CHROMIUM")
    if env:
        return env
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome",
                    "/opt/pw-browsers/chromium/chrome-linux*/chrome"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


class MotionRenderer:
    def __init__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "The motion style needs Playwright:\n  pip install playwright\n  playwright install chromium"
            ) from exc
        self._pw = sync_playwright().start()
        exe = _chromium_path()
        self._browser = self._pw.chromium.launch(executable_path=exe) if exe else self._pw.chromium.launch()
        self._page = self._browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        self._page.goto(TEMPLATE.resolve().as_uri())

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def prepare(self, payload: dict) -> dict:
        """Load a scene. Returns {'end': seconds of the last finite animation, 'panel': rect|None}."""
        self._page.goto(TEMPLATE.resolve().as_uri())
        self._page.evaluate("document.fonts.ready")
        return self._page.evaluate("sc => window.render(sc)", payload)

    def record(self, seconds: float, out_mp4: Path, audio: Path | None = None,
               overlay_frames: Path | None = None, overlay_rect: dict | None = None) -> Path:
        """Record the prepared scene for ``seconds`` into ``out_mp4`` (with audio / 3D overlay)."""
        n = max(1, int(round(seconds * FPS)))
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-"]
        if audio is not None:
            cmd += ["-i", str(audio)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        if overlay_frames is not None and overlay_rect:
            x, y = int(overlay_rect["x"]) + 3, int(overlay_rect["y"]) + 3
            w, h = int(overlay_rect["w"]) - 6, int(overlay_rect["h"]) - 6
            w, h = w // 2 * 2, h // 2 * 2
            cmd += ["-stream_loop", "-1", "-framerate", "24", "-i", str(overlay_frames / "f%04d.png")]
            graph = (f"[2:v]format=rgba,scale={w}:{h}:force_original_aspect_ratio=decrease[m];"
                     f"[0:v][m]overlay={x}+({w}-overlay_w)/2:{y}+({h}-overlay_h)/2:shortest=0,"
                     f"fps={FPS},format=yuv420p[v]")
            cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "1:a"]
        else:
            cmd += ["-map", "0:v", "-map", "1:a", "-vf", "format=yuv420p"]
        cmd += ["-t", f"{n / FPS:.3f}", "-af", f"apad,atrim=0:{n / FPS:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", str(out_mp4)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(n):
                self._page.evaluate("t => window.setTime(t)", i / FPS)
                proc.stdin.write(self._page.screenshot(type="jpeg", quality=92))
        finally:
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed while recording a scene:\n{err[-2000:]}")
        return out_mp4
