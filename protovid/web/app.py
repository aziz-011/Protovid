"""Protovid web app: upload a protocol, review the script scene by scene, render the video.

Run:  protovid-web            (or: uvicorn protovid.web.app:app --host 0.0.0.0 --port 8000)

Environment:
  PROTOVID_DATA       folder for projects (default ./protovid-data)
  PROTOVID_PASSWORD   if set, the app asks for this password (HTTP Basic, any user name)
  ANTHROPIC_API_KEY   needed for Claude-written scripts (otherwise use the offline mode)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..agent import build_storyboard, motion_payload, render_video
from ..images import IMAGE_EXTS, load_images
from ..render3d import MODEL_EXTS, ModelLibrary, load_model
from ..storyboard import Storyboard, StoryboardError

HERE = Path(__file__).parent
MOTION_DIR = HERE.parent / "motion"
DATA = Path(os.environ.get("PROTOVID_DATA", "protovid-data")).resolve()
PROJECTS = DATA / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Auth (optional)
# --------------------------------------------------------------------------- #

_basic = HTTPBasic(auto_error=False)


def require_auth(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    password = os.environ.get("PROTOVID_PASSWORD")
    if not password:
        return
    if creds is None or not secrets.compare_digest(creds.password.encode(), password.encode()):
        raise HTTPException(401, "Password required", headers={"WWW-Authenticate": 'Basic realm="Protovid"'})


app = FastAPI(title="Protovid", dependencies=[Depends(require_auth)])

# --------------------------------------------------------------------------- #
# Projects on disk
# --------------------------------------------------------------------------- #
#   projects/<id>/project.json      name, options, created
#   projects/<id>/input/            protocol.docx, photos/, models/
#   projects/<id>/storyboard.json   the script (edited in the app)
#   projects/<id>/renders/*.mp4

DEFAULT_OPTIONS = {
    "style": "motion", "use_ai": True, "language": "", "lang": "en", "audience": "", "context": "",
    "voice": "gtts", "doc_images": True, "model_up": "",
}
_ID = re.compile(r"^[a-f0-9]{12}$")


def _pdir(pid: str) -> Path:
    if not _ID.match(pid):
        raise HTTPException(404, "No such project")
    d = PROJECTS / pid
    if not (d / "project.json").exists():
        raise HTTPException(404, "No such project")
    return d


def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip() or "file"
    return name[:120]


def _read_project(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


def _write_project(d: Path, meta: dict) -> None:
    (d / "project.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_board(d: Path) -> Storyboard | None:
    f = d / "storyboard.json"
    return Storyboard.from_json(f.read_text(encoding="utf-8")) if f.exists() else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _project_summary(d: Path) -> dict:
    meta = _read_project(d)
    renders = sorted((d / "renders").glob("*.mp4")) if (d / "renders").exists() else []
    board = _load_board(d)
    return {
        **meta,
        "has_script": board is not None,
        "scenes": len(board.scenes) if board else 0,
        "renders": [{"name": r.name, "size": r.stat().st_size, "time": r.stat().st_mtime} for r in renders],
        "photos": sorted(p.name for p in (d / "input/photos").glob("*")) if (d / "input/photos").exists() else [],
        "models": sorted(p.name for p in (d / "input/models").glob("*")) if (d / "input/models").exists() else [],
        "jobs": [j.public() for j in JOBS.values() if j.project == meta["id"]][-5:],
    }


async def _save_uploads(files: list[UploadFile], folder: Path, allowed: set[str]) -> list[str]:
    saved = []
    for f in files or []:
        if not f or not f.filename:
            continue
        name = _safe_name(f.filename)
        if Path(name).suffix.lower() not in allowed:
            raise HTTPException(400, f"Unsupported file type: {name}")
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / name, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(name)
    return saved


# --------------------------------------------------------------------------- #
# Background jobs (one at a time: rendering is CPU-heavy)
# --------------------------------------------------------------------------- #

class Job:
    def __init__(self, project: str, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.project, self.kind = project, kind
        self.status, self.progress, self.message = "queued", 0.0, "Waiting..."
        self.log: list[str] = []
        self.result: str | None = None
        self.error: str | None = None
        self.created = time.time()

    def public(self) -> dict:
        return {"id": self.id, "project": self.project, "kind": self.kind, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message, "log": self.log[-30:],
                "result": self.result, "error": self.error, "created": self.created}

    def logger(self, line: str) -> None:
        self.log.append(line)
        m = re.match(r"\[(\d+)/(\d+)\]", line.strip())
        if m:
            i, n = int(m.group(1)), int(m.group(2))
            self.progress = (i - 1) / n
            self.message = line.strip()
        elif not line.startswith("  "):
            self.message = line.strip()


JOBS: dict[str, Job] = {}
_pool = ThreadPoolExecutor(max_workers=1)
_lock = threading.Lock()


def _submit(job: Job, fn) -> Job:
    JOBS[job.id] = job

    def run():
        job.status = "running"
        try:
            job.result = fn(job)
            job.status, job.progress = "done", 1.0
            job.message = "Done"
        except Exception as exc:  # report to the UI
            job.status, job.error = "error", f"{exc.__class__.__name__}: {exc}"
            job.message = job.error
            job.log.append(traceback.format_exc()[-1500:])
    _pool.submit(run)
    return job


def _library(d: Path, meta: dict) -> ModelLibrary | None:
    models = []
    up = meta["options"].get("model_up") or None
    for f in sorted((d / "input/models").glob("*")) if (d / "input/models").exists() else []:
        models.append(load_model(f, up=up))
    return ModelLibrary(models) if models else None


def _job_script(d: Path, meta: dict):
    def fn(job: Job) -> str:
        o = meta["options"]
        job.logger("Reading the protocol" + (" and asking Claude to write the script..." if o["use_ai"] else "..."))
        photos = load_images(d / "input/photos") if (d / "input/photos").exists() else []
        board = build_storyboard(
            d / "input" / meta["docx"], use_ai=o["use_ai"], language=o["language"] or None,
            audience=o["audience"] or None, context=o["context"] or None, images=photos,
            figures_dir=(d / "figures") if o["doc_images"] else None, models=_library(d, meta),
            style=o["style"], log=job.logger,
        )
        (d / "storyboard.json").write_text(board.to_json(), encoding="utf-8")
        return f"{len(board.scenes)} scenes"
    return fn


def _job_render(d: Path, meta: dict):
    def fn(job: Job) -> str:
        board = _load_board(d)
        if board is None:
            raise StoryboardError("Generate the script first")
        o = meta["options"]
        out = d / "renders" / f"{_safe_name(meta['name']).replace(' ', '_')}_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        work = d / "work"
        shutil.rmtree(work, ignore_errors=True)
        render_video(board, out, voice=o["voice"], lang=o["lang"] or "en", work_dir=work,
                     log=job.logger, style=o["style"])
        shutil.rmtree(work, ignore_errors=True)
        return out.name
    return fn


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((HERE / "index.html").read_text(encoding="utf-8"))


@app.get("/motion/template.html", response_class=HTMLResponse)
def motion_template() -> HTMLResponse:
    return HTMLResponse((MOTION_DIR / "template.html").read_text(encoding="utf-8"))


@app.get("/api/status")
def status() -> dict:
    return {"claude": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            "festival": shutil.which("text2wave") is not None,
            "espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak")),
            "ffmpeg": shutil.which("ffmpeg") is not None}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    out = []
    for d in sorted(PROJECTS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (d / "project.json").exists():
            out.append(_project_summary(d))
    return out


@app.post("/api/projects")
async def create_project(
    docx: UploadFile = File(...),
    photos: list[UploadFile] = File(default=[]),
    models: list[UploadFile] = File(default=[]),
    name: str = Form(""),
    options: str = Form("{}"),
) -> dict:
    if not docx.filename or not docx.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a Word .docx file")
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS / pid
    (d / "input").mkdir(parents=True)
    docx_name = await _save_uploads([docx], d / "input", {".docx"})
    await _save_uploads(photos, d / "input/photos", IMAGE_EXTS)
    await _save_uploads(models, d / "input/models", MODEL_EXTS)
    try:
        opts = {**DEFAULT_OPTIONS, **{k: v for k, v in json.loads(options or "{}").items() if k in DEFAULT_OPTIONS}}
    except json.JSONDecodeError:
        raise HTTPException(400, "Bad options")
    meta = {"id": pid, "name": name.strip() or Path(docx_name[0]).stem.replace("_", " "),
            "docx": docx_name[0], "created": time.time(), "options": opts}
    _write_project(d, meta)
    return _project_summary(d)


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    d = _pdir(pid)
    board = _load_board(d)
    return {**_project_summary(d), "storyboard": json.loads(board.to_json()) if board else None}


@app.patch("/api/projects/{pid}")
async def update_project(pid: str, request: Request) -> dict:
    d = _pdir(pid)
    body = await request.json()
    meta = _read_project(d)
    if "name" in body:
        meta["name"] = str(body["name"]).strip() or meta["name"]
    if "options" in body:
        meta["options"].update({k: v for k, v in body["options"].items() if k in DEFAULT_OPTIONS})
    _write_project(d, meta)
    return _project_summary(d)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    d = _pdir(pid)
    if any(j.project == pid and j.status in ("queued", "running") for j in JOBS.values()):
        raise HTTPException(409, "A job is still running for this project")
    shutil.rmtree(d)
    return {"deleted": pid}


@app.post("/api/projects/{pid}/files")
async def add_files(pid: str, photos: list[UploadFile] = File(default=[]),
                    models: list[UploadFile] = File(default=[])) -> dict:
    d = _pdir(pid)
    await _save_uploads(photos, d / "input/photos", IMAGE_EXTS)
    await _save_uploads(models, d / "input/models", MODEL_EXTS)
    board = _load_board(d)
    if board is not None:  # make new photos/models usable in the existing script
        for a in load_images(d / "input/photos"):
            board.images.setdefault(a.name, str(a.path))
        lib = _library(d, _read_project(d))
        for m in (lib.models.values() if lib else []):
            board.models[m.name] = {"path": str(m.path), "up": m.up}
        (d / "storyboard.json").write_text(board.to_json(), encoding="utf-8")
    return _project_summary(d)


@app.put("/api/projects/{pid}/storyboard")
async def save_storyboard(pid: str, request: Request) -> dict:
    d = _pdir(pid)
    data = await request.json()
    old = _load_board(d)
    try:
        board = Storyboard.from_json(json.dumps(data))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid script: {exc}")
    # never let the browser point images/models outside the project
    if old is not None:
        board.images, board.models = old.images, old.models
    (d / "storyboard.json").write_text(board.to_json(), encoding="utf-8")
    return {"saved": True, "scenes": len(board.scenes)}


@app.post("/api/projects/{pid}/script")
def generate_script(pid: str) -> dict:
    d = _pdir(pid)
    meta = _read_project(d)
    if meta["options"]["use_ai"] and not status()["claude"]:
        raise HTTPException(400, "No ANTHROPIC_API_KEY on the server: set it, or untick 'Use Claude'")
    return _submit(Job(pid, "script"), _job_script(d, meta)).public()


@app.post("/api/projects/{pid}/render")
def render(pid: str) -> dict:
    d = _pdir(pid)
    if _load_board(d) is None:
        raise HTTPException(400, "Generate the script first")
    return _submit(Job(pid, "render"), _job_render(d, _read_project(d))).public()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job")
    return job.public()


@app.get("/api/projects/{pid}/scenes/{index}/payload")
def scene_payload(pid: str, index: int) -> dict:
    d = _pdir(pid)
    board = _load_board(d)
    if board is None or not (0 <= index < len(board.scenes)):
        raise HTTPException(404, "No such scene")
    return _payload(pid, board, board.scenes[index])


@app.post("/api/projects/{pid}/preview")
async def preview_payload(pid: str, request: Request) -> dict:
    """Payload for an edited (unsaved) scene, so the editor previews changes live."""
    d = _pdir(pid)
    board = _load_board(d)
    if board is None:
        raise HTTPException(404, "No script yet")
    body = await request.json()
    tmp = Storyboard.from_json(json.dumps({"title": board.title, "scenes": body.get("scenes") or [body["scene"]]}))
    tmp.images, tmp.models, tmp.labels, tmp.machine_name = board.images, board.models, board.labels, board.machine_name
    return _payload(pid, tmp, tmp.scenes[int(body.get("index", 0))] if body.get("scenes") else tmp.scenes[0])


def _payload(pid: str, board: Storyboard, scene) -> dict:
    by_path = {str(Path(p).resolve()): n for n, p in board.images.items()}

    def url_for(path: Path) -> str:
        return f"/api/projects/{pid}/image/{by_path.get(str(path.resolve()), path.name)}"
    has3d = bool(scene.model_part and board.models)
    return motion_payload(board, scene, has3d=has3d, url_for=url_for)


@app.get("/api/projects/{pid}/image/{name}")
def image(pid: str, name: str) -> FileResponse:
    d = _pdir(pid)
    board = _load_board(d)
    path = board.image_path(name) if board else None
    if path is None or not _inside(path, d):
        raise HTTPException(404, "No such image")
    return FileResponse(path)


@app.get("/api/projects/{pid}/renders/{name}")
def get_render(pid: str, name: str, download: bool = False) -> FileResponse:
    d = _pdir(pid)
    path = d / "renders" / _safe_name(name)
    if not path.exists() or not _inside(path, d):
        raise HTTPException(404, "No such video")
    return FileResponse(path, media_type="video/mp4", filename=path.name if download else None)


@app.exception_handler(StoryboardError)
def _sb_error(_: Request, exc: StoryboardError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def main() -> None:
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(prog="protovid-web", description="Run the Protovid web app")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = ap.parse_args()
    print(f"Protovid web app on http://{args.host}:{args.port}  (projects in {PROJECTS})")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
