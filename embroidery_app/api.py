"""HTTP API + static web UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import service
from .ai import AIError
from .ai import catalog as ai_catalog
from .ai.tasks import CLEAN_MODES
from .export import FORMATS
from .fabrics import FABRICS
from .ingest.raster import RasterOptions
from .ingest.vector import VectorOptions
from .models import STITCH_STYLES, StitchParams

log = logging.getLogger("embroidery")
STATIC = Path(__file__).parent / "static"

HOOPS = [
    {"name": "100 x 100 mm", "width": 100, "height": 100},
    {"name": "150 x 150 mm", "width": 150, "height": 150},
    {"name": "200 x 200 mm", "width": 200, "height": 200},
    {"name": "300 x 300 mm", "width": 300, "height": 300},
    {"name": "360 x 200 mm", "width": 360, "height": 200},
    {"name": "Cap 70 x 140 mm", "width": 140, "height": 70},
    {"name": "Left chest ~90 mm", "width": 90, "height": 90},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    removed = service.cleanup_old_projects()
    if removed:
        log.info("Removed %d old projects", removed)
    yield


app = FastAPI(title="AI Embroidery Digitizer", version="1.0.0", lifespan=lifespan)


@app.exception_handler(service.NotFound)
async def _not_found(_: Request, exc: service.NotFound):
    return JSONResponse({"detail": "Project not found"}, status_code=404)


@app.exception_handler(ValueError)
async def _bad_request(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(AIError)
async def _ai_error(_: Request, exc: AIError):
    return JSONResponse({"detail": f"AI service: {exc}"}, status_code=502)


class ExtractBody(BaseModel):
    options: dict[str, Any] = {}


class LayersBody(BaseModel):
    layers: list[dict[str, Any]]


class MergeBody(BaseModel):
    source: int
    target: int


class StitchBody(BaseModel):
    params: dict[str, Any] = {}


class ObjectsBody(BaseModel):
    ids: list[str] = []
    scope: str = "selected"  # selected | colour | all
    stitch_type: Optional[str] = None  # None = unchanged, "inherit" = same as colour
    angle: Optional[Union[float, str]] = None  # None = unchanged, "inherit" = same as colour
    enabled: Optional[bool] = None
    spacing_mm: Optional[Union[float, str]] = None  # thickness (row / stitch gap), "inherit" = design setting
    pull_mm: Optional[Union[float, str]] = None  # extra width, "inherit" = design setting
    bold: Optional[Union[bool, str]] = None  # outlines 3x / once, "inherit" = design setting
    underlay_style: Optional[str] = None  # foundation type (see stitch/underlay.py), "inherit" = design setting


class RecolorBody(BaseModel):
    ids: list[str] = []
    target: Optional[int] = None  # existing colour index, or
    hex: Optional[str] = None  # a thread colour (#rrggbb)


class ShapeEditBody(BaseModel):
    ids: list[str] = []
    op: str  # add | erase | grow | shrink | smooth
    tool: str = "brush"  # brush | lasso
    points: list[list[float]] = []  # mm, design coordinates
    size_mm: float = 1.0


class ResetBody(BaseModel):
    ids: Optional[list[str]] = None  # None / empty = every shape


class AISettings(BaseModel):
    """The user's choice of AI service. The key is used for this request only."""

    provider: Optional[str] = None  # None / "builtin" = no AI, built-in algorithms
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    edit_model: Optional[str] = None
    vision_model: Optional[str] = None

    def as_dict(self) -> Optional[dict]:
        return None if not self.provider or self.provider == "builtin" else self.model_dump()


class AIBody(BaseModel):
    ai: Optional[AISettings] = None
    provider: Optional[str] = None  # older clients: server-configured provider by name
    mode: str = "redraw"  # clean-up type
    colors: Optional[int] = None
    width_mm: Optional[float] = None
    refresh: bool = False

    def settings(self) -> Optional[dict]:
        if self.ai is not None:
            return self.ai.as_dict()
        return {"provider": self.provider} if self.provider and self.provider != "builtin" else None


@app.get("/api/config")
def get_config():
    return {
        "formats": FORMATS,
        "raster_defaults": asdict(RasterOptions()),
        "vector_defaults": asdict(VectorOptions()),
        "stitch_defaults": StitchParams().to_dict(),
        "fabrics": [f.to_dict() for f in FABRICS],
        "stitch_styles": [list(s) for s in STITCH_STYLES],
        "ai": ai_catalog(),
        "ai_clean_modes": list(CLEAN_MODES),
        "hoops": HOOPS,
    }


@app.post("/api/ai/models")
def ai_models(body: AIBody):
    """Live model list of a provider (with the user's key) + recommended picks."""
    ai = body.settings()
    if not ai:
        raise ValueError("Choose an AI service first.")
    return service.ai_models(ai, body.refresh)


@app.post("/api/projects")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise ValueError("Empty file.")
    return service.create_project(file.filename or "design", data)


@app.get("/api/projects/{pid}/source")
def source(pid: str):
    path, mime = service.source_file(pid)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{pid}/original")
def original(pid: str):
    path, mime = service.original_file(pid)
    return FileResponse(path, media_type=mime)


@app.post("/api/projects/{pid}/extract")
def extract(pid: str, body: ExtractBody):
    return service.extract(pid, body.options)


@app.get("/api/projects/{pid}/design")
def design(pid: str):
    return service.design_payload(service.load_design(pid), pid)


@app.put("/api/projects/{pid}/objects")
def update_objects(pid: str, body: ObjectsBody):
    return service.update_objects(
        pid, body.ids, body.scope, body.stitch_type, body.angle, body.enabled,
        spacing_mm=body.spacing_mm, pull_mm=body.pull_mm, bold=body.bold, underlay_style=body.underlay_style,
    )


@app.post("/api/projects/{pid}/objects/reset")
def reset_objects(pid: str, body: ResetBody):
    return service.reset_objects(pid, body.ids)


@app.post("/api/projects/{pid}/undo")
def undo(pid: str):
    return service.undo(pid)


@app.post("/api/projects/{pid}/redo")
def redo(pid: str):
    return service.redo(pid)


@app.post("/api/projects/{pid}/objects/recolor")
def recolor(pid: str, body: RecolorBody):
    return service.recolor_objects(pid, body.ids, body.target, body.hex)


@app.post("/api/projects/{pid}/objects/edit")
def edit_shapes(pid: str, body: ShapeEditBody):
    return service.edit_shapes(pid, body.ids, body.op, body.tool, body.points, body.size_mm)


@app.get("/api/projects/{pid}/reference/{kind}")
def reference(pid: str, kind: str):
    data, mime = service.reference_file(pid, kind)
    return Response(data, media_type=mime, headers={"Cache-Control": "no-cache"})


@app.put("/api/projects/{pid}/layers")
def layers(pid: str, body: LayersBody):
    return service.update_layers(pid, body.layers)


@app.post("/api/projects/{pid}/layers/merge")
def merge(pid: str, body: MergeBody):
    return service.merge_layers(pid, body.source, body.target)


@app.post("/api/projects/{pid}/stitch")
def stitch(pid: str, body: StitchBody):
    return service.stitch(pid, body.params)


@app.get("/api/projects/{pid}/export/{fmt}")
def export(pid: str, fmt: str, formats: Optional[str] = None):
    """``formats`` (comma separated, only with ``zip``): the files to put in the archive."""
    data, filename, mime = service.export(pid, fmt, service.parse_formats(formats) if fmt.lower() == "zip" else None)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/projects/{pid}/ai/clean")
def ai_clean(pid: str, body: AIBody):
    return service.ai_clean(pid, body.settings(), body.mode, body.colors)


@app.post("/api/projects/{pid}/ai/revert")
def ai_revert(pid: str):
    return service.ai_revert(pid)


@app.post("/api/projects/{pid}/ai/suggest")
def ai_suggest(pid: str, body: AIBody):
    """Recommended preparation settings: built-in analysis, refined by AI when configured."""
    return service.ai_suggest(pid, body.settings(), body.width_mm)


@app.post("/api/projects/{pid}/ai/analyze")
def ai_analyze(pid: str, body: AIBody):
    """Older name of /ai/suggest, kept for compatibility."""
    return service.ai_suggest(pid, body.settings(), body.width_mm)


@app.post("/api/projects/{pid}/ai/review")
def ai_review(pid: str, body: AIBody):
    """Check the generated stitches: built-in rules, plus AI review when configured."""
    return service.ai_review(pid, body.settings())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
