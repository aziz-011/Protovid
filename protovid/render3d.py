"""3D views from CAD models: rotating, close-up and exploded animations.

Supported files: STEP/STP (needs ``cascadio``), GLB/GLTF, OBJ, STL, PLY, 3MF.
Each part keeps the name it has in the CAD assembly; instances of the same
part ("Screw M4:1", "Screw M4:2" ...) are grouped under one name so they are
highlighted together.

Rendering uses PyVista (VTK) off-screen. Frames are written as transparent
PNGs and composited onto the slide by ``video.make_clip``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MODEL_EXTS = {".step", ".stp", ".glb", ".gltf", ".obj", ".stl", ".ply", ".3mf", ".off"}
VIEWS = ["orbit", "closeup", "exploded"]
FPS = 24
FRAMES = {"orbit": 144, "closeup": 96, "exploded": 120}  # seamless loops
MAX_PARTS_FOR_CLAUDE = 300

GHOST = (0.80, 0.83, 0.87)
STEEL = (0.62, 0.66, 0.72)


def _base_name(name: str) -> str:
    """'Screw M4:3' / 'Screw_M4-2' / 'Screw M4 (2)' / 'geometry_0' -> 'Screw M4' / 'Screw_M4' ..."""
    n = re.sub(r"\.(stl|step|stp|obj|glb)$", "", name.strip(), flags=re.I)
    n = re.sub(r"(\s*[:#]\s*\d+|\s*\(\d+\)|[-_.]\d{1,3})$", "", n)
    return n.strip() or name


@dataclass
class Model3D:
    name: str                                   # short id, from the file name
    path: Path
    parts: dict[str, list] = field(default_factory=dict)   # part name -> list of trimesh.Trimesh (world coords)
    up: str = "z"

    @property
    def part_names(self) -> list[str]:
        return list(self.parts)

    def bounds(self, names: list[str] | None = None) -> np.ndarray:
        meshes = [m for n in (names or self.parts) for m in self.parts.get(n, [])]
        if not meshes:
            meshes = [m for ms in self.parts.values() for m in ms]
        lo = np.min([m.bounds[0] for m in meshes], axis=0)
        hi = np.max([m.bounds[1] for m in meshes], axis=0)
        return np.array([lo, hi])


def load_model(path: str | Path, up: str | None = None) -> Model3D:
    import trimesh

    path = Path(path)
    if path.suffix.lower() not in MODEL_EXTS:
        raise ValueError(f"Unsupported 3D file: {path.name} (use STEP, GLB, OBJ or STL)")
    if path.suffix.lower() in (".step", ".stp"):
        try:
            import cascadio  # noqa: F401  (lets trimesh read STEP)
        except ImportError as exc:
            raise RuntimeError("Reading STEP files needs: pip install cascadio") from exc

    scene = trimesh.load(str(path), force="scene")
    parts: dict[str, list] = {}
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geom = scene.geometry.get(geom_name)
        if geom is None or not hasattr(geom, "faces") or len(geom.faces) == 0:
            continue
        mesh = geom.copy()
        mesh.apply_transform(transform)
        parts.setdefault(_base_name(str(node)), []).append(mesh)
    if not parts:
        raise ValueError(f"No geometry found in {path.name}")

    if up is None:  # glTF/GLB are Y-up by spec (STEP read through cascadio comes in as GLB too)
        up = "y" if path.suffix.lower() in (".glb", ".gltf", ".step", ".stp", ".obj") else "z"
    return Model3D(name=_slug(path.stem), path=path.resolve(), parts=parts, up=up)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "model"


# --------------------------------------------------------------------------- #
# Registry used by the storyboard: "model/part" ids
# --------------------------------------------------------------------------- #

class ModelLibrary:
    def __init__(self, models: list[Model3D]):
        self.models = {m.name: m for m in models}

    def __bool__(self) -> bool:
        return bool(self.models)

    def part_ids(self) -> list[str]:
        """'model' (whole assembly) and 'model/part' ids, capped for the API schema."""
        ids: list[str] = []
        for m in self.models.values():
            ids.append(m.name)
            ids.extend(f"{m.name}/{p}" for p in m.part_names)
        return ids[:MAX_PARTS_FOR_CLAUDE]

    def resolve(self, part_id: str | None) -> tuple[Model3D, list[str]] | None:
        if not part_id:
            return None
        model_name, _, part = part_id.partition("/")
        model = self.models.get(model_name)
        if model is None:
            return None
        if not part:
            return model, []
        return (model, [part]) if part in model.parts else None

    def describe(self) -> str:
        lines = []
        for m in self.models.values():
            lines.append(f"3D model '{m.name}' ({m.path.name}), parts:")
            lines.extend(f"  - {m.name}/{p}" for p in m.part_names[:MAX_PARTS_FOR_CLAUDE])
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _to_pv(mesh):
    import pyvista as pv

    faces = np.hstack([np.full((len(mesh.faces), 1), 3), mesh.faces]).astype(np.int64).ravel()
    return pv.PolyData(np.asarray(mesh.vertices, dtype=float), faces)


def render_frames(model: Model3D, highlight: list[str], view: str, out_dir: Path,
                  size: tuple[int, int] = (860, 820),
                  accent: tuple[int, int, int] = (14, 116, 144)) -> list[Path]:
    """Render a seamless loop of transparent PNG frames. Returns the frame paths."""
    import pyvista as pv

    view = view if view in VIEWS else "orbit"
    n_frames = FRAMES[view]
    out_dir.mkdir(parents=True, exist_ok=True)

    up_vec = np.array([0, 0, 1.0]) if model.up == "z" else np.array([0, 1.0, 0])
    full = model.bounds()
    centre_all = full.mean(axis=0)
    diag_all = float(np.linalg.norm(full[1] - full[0])) or 1.0
    focus = model.bounds(highlight) if highlight else full
    centre = focus.mean(axis=0)
    diag = float(np.linalg.norm(focus[1] - focus[0])) or diag_all

    pl = pv.Plotter(off_screen=True, window_size=list(size))
    pl.set_background("white")
    pl.enable_anti_aliasing("ssaa")
    pl.enable_depth_peeling(number_of_peels=6)
    pl.add_light(pv.Light(light_type="headlight", intensity=0.55))
    accent_rgb = tuple(c / 255 for c in accent)

    actors = []  # (actor, explode direction)
    for name, meshes in model.parts.items():
        hot = name in highlight
        for mesh in meshes:
            color = accent_rgb if hot else (GHOST if highlight else STEEL)
            opacity = 1.0 if (hot or not highlight) else 0.18
            actor = pl.add_mesh(_to_pv(mesh), color=color, opacity=opacity, smooth_shading=True, split_sharp_edges=True,
                                specular=0.35, specular_power=20, show_edges=False)
            direction = np.asarray(mesh.centroid) - centre_all
            actors.append((actor, direction))

    # Camera: distance chosen so the focus fits; close-ups keep some context
    dist = max(diag * 2.8, diag_all * 0.9) if view == "closeup" else diag_all * 1.7
    elevation = math.radians(22)
    # a horizontal "front" direction perpendicular to up
    side = np.array([1.0, 0, 0]) if model.up == "z" else np.array([0, 0, 1.0])
    front = np.cross(up_vec, side)
    look_at = centre if view == "closeup" else centre_all
    if view == "closeup" and highlight:
        # Look at the part from the side it faces out of the assembly, so it isn't hidden or edge-on
        outward = centre - centre_all
        outward = outward - np.dot(outward, up_vec) * up_vec  # keep it horizontal
        if np.linalg.norm(outward) > 1e-6 * diag_all:
            outward /= np.linalg.norm(outward)
            front = outward
            side = np.cross(front, up_vec)

    frames: list[Path] = []
    for i in range(n_frames):
        t = i / n_frames
        if view == "orbit":
            az = 2 * math.pi * t + math.radians(30)
        else:  # gentle rocking keeps the part facing the viewer
            base = 15 if view == "closeup" else 30
            az = math.radians(base) + math.radians(30) * math.sin(2 * math.pi * t)
        if view == "exploded":
            k = 0.55 * (1 - math.cos(2 * math.pi * t)) / 2  # 0 -> 0.55 -> 0
            for actor, d in actors:
                actor.SetPosition(*(d * k))
        horiz = math.cos(az) * front + math.sin(az) * side
        cam_dir = math.cos(elevation) * horiz + math.sin(elevation) * up_vec
        pl.camera_position = [tuple(look_at + cam_dir * dist), tuple(look_at), tuple(up_vec)]
        pl.camera.view_angle = 30
        pl.reset_camera_clipping_range()
        pl.render()
        path = out_dir / f"f{i:04d}.png"
        pl.screenshot(str(path), transparent_background=True)
        frames.append(path)
    pl.close()
    return frames


def match_part(text: str, library: ModelLibrary) -> str | None:
    """Offline: find the part whose name words appear in the step text."""
    from .images import _words

    words = _words(text)
    best, best_score = None, 0.0
    for pid in library.part_ids():
        if "/" not in pid:
            continue
        label = _words(re.sub(r"[_\-]+", " ", pid.split("/", 1)[1]))
        if not label:
            continue
        hits = sum(1 for w in label if w in words or w.rstrip("s") in words)
        score = hits / len(label)
        needed = 1.0 if len(label) <= 2 else 0.6
        if score >= needed and score > best_score:
            best, best_score = pid, score
    return best
