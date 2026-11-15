"""HTTP API + static web UI.

Every route except the config, sign-in and static files needs a login (``protected``).
A project id in the URL must belong to the signed-in account (checked once, in
auth.require_user). The Super Admin console lives in admin_api.py.

Server-side rules on top of the workflow:
- generations (first stitch-output download of a design) go through usage.generation:
  allowance checked and charged atomically, never charged on errors;
- plan features are checked here (formats, ZIP, photo stitch) and in user_ai (AI);
- per-account rate limits for work, downloads and AI;
- projects are temporary (temp_projects): a new upload replaces the session's previous
  one, and pages release them when they go away.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, entitlements, maintenance, service, temp_projects, usage, user_ai
from .ai import AIError
from .ai import catalog as ai_catalog
from .ai.tasks import CLEAN_MODES
from .config import settings
from .entitlements import EntitlementError
from .export import FORMATS
from .fabrics import FABRICS
from .ingest.raster import RasterOptions
from .ingest.vector import VectorOptions
from .models import STITCH_STYLES, STITCH_TERMS, StitchParams
from .stitch import decorative

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
    auth.ensure_super_admin()
    maintenance.start()  # temporary projects, stale reservations, expiries, retention
    yield
    maintenance.stop()


app = FastAPI(title="AI Embroidery Digitizer", version="1.1.0", lifespan=lifespan)


@app.exception_handler(service.NotFound)
async def _not_found(_: Request, exc: service.NotFound):
    return JSONResponse({"detail": "Project not found. It may have been closed or removed after a period of inactivity."},
                        status_code=404)


@app.exception_handler(ValueError)
async def _bad_request(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(LookupError)
async def _lookup(_: Request, exc: LookupError):
    return JSONResponse({"detail": str(exc.args[0]) if exc.args else "Not found."}, status_code=404)


@app.exception_handler(AIError)
async def _ai_error(_: Request, exc: AIError):
    return JSONResponse({"detail": f"AI service: {exc}"}, status_code=502)


@app.exception_handler(EntitlementError)
async def _entitlement(_: Request, exc: EntitlementError):
    return JSONResponse({"detail": str(exc), "code": exc.code, "usage": exc.usage}, status_code=exc.status)


@app.exception_handler(usage.RateLimited)
async def _rate_limited(_: Request, exc: usage.RateLimited):
    return JSONResponse({"detail": str(exc), "code": "rate_limited"}, status_code=429, headers={"Retry-After": "60"})


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
    guide: Optional[Union[list[list[float]], str]] = None  # guide line (mm), "inherit" = remove it
    blend_to: Optional[Union[int, str]] = None  # blend fill: colour index to fade into, "inherit" = none


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
    """The user's choice of AI service. Keys always come from the account's saved settings."""

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


# --------------------------------------------------------------------------- guards

_BUCKETS = (("/ai/", "ai"), ("/export/", "export"))


def _rate_limits(request: Request, user: dict = Depends(auth.require_user)) -> None:
    """Per-account limits on everything that does work (reads of a page are free)."""
    path = request.url.path
    bucket = next((b for part, b in _BUCKETS if part in path), None)
    if bucket is None and request.method in ("GET", "HEAD"):
        return
    usage.rate_limit(user["id"], bucket or "work")


protected = APIRouter(dependencies=[Depends(auth.require_user), Depends(_rate_limits)])


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """State-changing requests from another website are refused (with SameSite cookies,
    a second guard against cross-site request forgery)."""
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    return await call_next(request)


@app.get("/api/config")
def get_config():
    return {
        "formats": FORMATS,
        "raster_defaults": asdict(RasterOptions()),
        "vector_defaults": asdict(VectorOptions()),
        "stitch_defaults": StitchParams().to_dict(),
        "fabrics": [f.to_dict() for f in FABRICS],
        "stitch_styles": [list(s) for s in STITCH_STYLES],
        "stitch_terms": STITCH_TERMS,  # trade names, shown next to the labels
        "motifs": [[k, m["label"]] for k, m in decorative.motifs().items()],
        "ai": ai_catalog(),
        "ai_clean_modes": list(CLEAN_MODES),
        "hoops": HOOPS,
        "session_days": auth.session_choices()[-1],  # longest "keep me signed in"
        "session_choices": auth.session_choices(),  # 0 = this browser session
    }


@protected.post("/api/ai/models")
def ai_models(body: AIBody, user: dict = Depends(auth.require_user)):
    """Live model list of a provider (with the user's key) + recommended picks. Also the
    "test connection" of the AI settings screen."""
    entitlements.require_feature(user["id"], "ai_services")
    ai = user_ai.for_models(user["id"], body.ai.model_dump() if body.ai else body.settings())
    if not ai:
        raise ValueError("Choose an AI service first.")
    with usage.track(user["id"], "ai_test", ai["provider"]) as rec:
        rec["provider"] = ai["provider"]
        return service.ai_models(ai, body.refresh)


# --------------------------------------------------------------------------- projects

@protected.post("/api/projects")
async def upload(request: Request, file: UploadFile = File(...), user: dict = Depends(auth.require_user)):
    data = await file.read()
    if not data:
        raise ValueError("Empty file.")
    meta = service.create_project(file.filename or "design", data, owner=user["id"])
    # a new project replaces the one this browser session had open (its files are deleted)
    temp_projects.register(meta["id"], user["id"], user.get("sid"))
    return meta


@protected.delete("/api/projects/{pid}")
def close_project(pid: str):
    """"Close design": delete the project and its files now."""
    temp_projects.delete(pid)
    return {"deleted": True}


@app.post("/api/projects/{pid}/release")
def release_project(pid: str, request: Request):
    """Sent by the page when it goes away (pagehide beacon). The project is deleted after a
    short grace period unless the page comes back (a reload reopens it). Answers 204 even
    without a valid session: a beacon cannot handle errors and must not reveal anything."""
    user = auth.session_user(request.cookies.get(auth.COOKIE))
    if user:
        temp_projects.release(pid, user["id"])
    return Response(status_code=204)


@protected.get("/api/projects/{pid}/source")
def source(pid: str):
    path, mime = service.source_file(pid)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


@protected.get("/api/projects/{pid}/original")
def original(pid: str):
    path, mime = service.original_file(pid)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


@protected.post("/api/projects/{pid}/extract")
def extract(pid: str, body: ExtractBody):
    return service.extract(pid, body.options)


@protected.get("/api/projects/{pid}/design")
def design(pid: str):
    return service.design_payload(service.load_design(pid), pid)


@protected.put("/api/projects/{pid}/objects")
def update_objects(pid: str, body: ObjectsBody):
    return service.update_objects(
        pid, body.ids, body.scope, body.stitch_type, body.angle, body.enabled,
        spacing_mm=body.spacing_mm, pull_mm=body.pull_mm, bold=body.bold, underlay_style=body.underlay_style,
        guide=body.guide, blend_to=body.blend_to,
    )


@protected.post("/api/projects/{pid}/objects/reset")
def reset_objects(pid: str, body: ResetBody):
    return service.reset_objects(pid, body.ids)


@protected.post("/api/projects/{pid}/undo")
def undo(pid: str):
    return service.undo(pid)


@protected.post("/api/projects/{pid}/redo")
def redo(pid: str):
    return service.redo(pid)


@protected.post("/api/projects/{pid}/objects/recolor")
def recolor(pid: str, body: RecolorBody):
    return service.recolor_objects(pid, body.ids, body.target, body.hex)


@protected.post("/api/projects/{pid}/objects/edit")
def edit_shapes(pid: str, body: ShapeEditBody):
    return service.edit_shapes(pid, body.ids, body.op, body.tool, body.points, body.size_mm)


@protected.get("/api/projects/{pid}/reference/{kind}")
def reference(pid: str, kind: str):
    data, mime = service.reference_file(pid, kind)
    return Response(data, media_type=mime, headers={"Cache-Control": "no-cache"})


@protected.put("/api/projects/{pid}/layers")
def layers(pid: str, body: LayersBody):
    return service.update_layers(pid, body.layers)


@protected.post("/api/projects/{pid}/layers/merge")
def merge(pid: str, body: MergeBody):
    return service.merge_layers(pid, body.source, body.target)


@protected.post("/api/projects/{pid}/stitch")
def stitch(pid: str, body: StitchBody, user: dict = Depends(auth.require_user)):
    if StitchParams.from_dict(body.params).photo_stitch:
        entitlements.require_feature(user["id"], "photo_stitch")
    return service.stitch(pid, body.params)


# downloads that are a generation (svg = artwork only); "wilcom" = the package for continuing
# in Wilcom (DST + artwork, see service.wilcom_package): its machine file is DST, so it needs no
# "all_formats", and it is one download, so it needs no "zip_export"
STITCH_OUTPUT = set(FORMATS) | {"png", "txt", service.WILCOM_PACKAGE}


def _check_export_features(user_id: int, pid: str, files: list[str]) -> bool:
    """Plan features for these files; True when they include stitch output (a generation)."""
    ent = entitlements.effective(user_id)
    extra = [f.upper() for f in files if f in FORMATS and f != "dst"]
    if extra and not ent.has("all_formats"):
        raise EntitlementError(f"{', '.join(extra)} downloads are not included in your {ent.plan['name']} plan - "
                               "DST is. Ask your administrator if you need other formats.", "feature_unavailable")
    stitched = any(f in STITCH_OUTPUT for f in files)
    if stitched and service.saved_stitch_params(pid).photo_stitch and not ent.has("photo_stitch"):
        raise EntitlementError(f"Photo stitch is not included in your {ent.plan['name']} plan.", "feature_unavailable")
    return stitched


@protected.get("/api/projects/{pid}/export/{fmt}")
def export(pid: str, fmt: str, formats: Optional[str] = None, user: dict = Depends(auth.require_user)):
    """``formats`` (comma separated, only with ``zip``): the files to put in the archive.
    The first download of stitch output for a design counts as one generation."""
    fmt = fmt.lower()
    chosen = service.parse_formats(formats) if fmt == "zip" else None
    if fmt == "zip":
        entitlements.require_feature(user["id"], "zip_export")
    files = (chosen or list(service.ZIP_DEFAULT)) if fmt == "zip" else [fmt]
    if fmt != "zip" and fmt not in STITCH_OUTPUT and fmt != "svg":
        raise ValueError(f"Unknown export format '{fmt}'")
    if _check_export_features(user["id"], pid, files):
        with usage.generation(user["id"], pid, f"export:{fmt}"):
            data, filename, mime = service.export(pid, fmt, chosen)
    else:
        data, filename, mime = service.export(pid, fmt, chosen)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                                    "Cache-Control": "no-store"})


@protected.get("/api/projects/{pid}/quota")
def project_quota(pid: str, user: dict = Depends(auth.require_user)):
    """Before downloading: is this design already paid for, and what is left?"""
    ent = entitlements.effective(user["id"])
    return {"charged": usage.already_charged(user["id"], pid), "usage": ent.usage_summary(),
            "features": {k: ent.has(k) for k in ("all_formats", "zip_export", "photo_stitch")}}


# --------------------------------------------------------------------------- AI assistant

def _ai_task(user: dict, kind: str, body: AIBody, run):
    """Run an AI-assisted task with the account's own AI settings and record it (provider,
    model, duration, tokens; never the prompt or the picture)."""
    ai = user_ai.resolve(user["id"], body.settings())
    with usage.track(user["id"], kind, "ai" if ai else "builtin") as rec:
        rec["provider"] = ai["provider"] if ai else "builtin"
        result = run(ai)
        if isinstance(result, dict):
            if result.get("model"):
                rec["model"] = result["model"]
            if result.get("ai_error"):
                rec["status"], rec["error"] = "failed", "ai_fallback"
            ai_meta = result.get("ai_cleaned")
            if isinstance(ai_meta, dict):
                rec["model"] = ai_meta.get("model")
        return result


@protected.post("/api/projects/{pid}/ai/clean")
def ai_clean(pid: str, body: AIBody, user: dict = Depends(auth.require_user)):
    return _ai_task(user, "ai_clean", body, lambda ai: service.ai_clean(pid, ai, body.mode, body.colors))


@protected.post("/api/projects/{pid}/ai/revert")
def ai_revert(pid: str):
    return service.ai_revert(pid)


@protected.post("/api/projects/{pid}/ai/suggest")
def ai_suggest(pid: str, body: AIBody, user: dict = Depends(auth.require_user)):
    """Recommended preparation settings: built-in analysis, refined by AI when configured."""
    return _ai_task(user, "ai_suggest", body, lambda ai: service.ai_suggest(pid, ai, body.width_mm))


@protected.post("/api/projects/{pid}/ai/analyze")
def ai_analyze(pid: str, body: AIBody, user: dict = Depends(auth.require_user)):
    """Older name of /ai/suggest, kept for compatibility."""
    return _ai_task(user, "ai_suggest", body, lambda ai: service.ai_suggest(pid, ai, body.width_mm))


@protected.post("/api/projects/{pid}/ai/review")
def ai_review(pid: str, body: AIBody, user: dict = Depends(auth.require_user)):
    """Check the generated stitches: built-in rules, plus AI review when configured."""
    return _ai_task(user, "ai_review", body, lambda ai: service.ai_review(pid, ai))


# --------------------------------------------------------------------------- accounts

class LoginBody(BaseModel):
    login: str = ""  # email or username
    email: str = ""  # older clients
    password: str = ""
    remember: bool = False  # older clients: the longest choice
    days: Optional[int] = None  # 0 = this browser session; else one of /api/config session_choices


class PasswordBody(BaseModel):
    current: str = ""
    password: str = ""


class ProfileBody(BaseModel):
    display_name: Optional[str] = None


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    user, token, expires, days = auth.login(body.login or body.email, body.password, body.remember, ip, body.days,
                                            request.headers.get("user-agent"))
    auth.set_cookie(response, request, token, days)
    return {"user": user, "expires_at": expires, "days": days}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    """Sign out: the session ends on the server and its temporary projects are deleted."""
    auth.logout(request.cookies.get(auth.COOKIE))
    auth.clear_cookie(response)
    return {"ok": True}


@protected.get("/api/auth/me")
def me(user: dict = Depends(auth.require_user)):
    return {"user": {k: v for k, v in user.items() if k != "sid"}, "session_days": auth.session_choices()[-1]}


@protected.post("/api/auth/password")
def change_password(body: PasswordBody, request: Request, user: dict = Depends(auth.require_user)):
    """Your own password: needs the current one; your other sessions end."""
    auth.change_own_password(user["id"], body.current, body.password, request.cookies.get(auth.COOKIE))
    return {"ok": True}


@protected.get("/api/me/account")
def my_account(user: dict = Depends(auth.require_user)):
    """The account page: plan, allowance, features, AI status and sessions."""
    ent = entitlements.effective(user["id"])
    plan = ent.plan
    return {
        "user": {k: v for k, v in user.items() if k != "sid"},
        "plan": {"name": plan["name"], "description": plan["description"], "code": plan["code"]},
        "subscription": {"status": ent.subscription["status"], "started_at": ent.subscription["started_at"],
                         "expires_at": ent.subscription["expires_at"], "expired": ent.subscription["expired"],
                         "assigned_plan": (ent.subscription["plan"] or {}).get("name")},
        "usage": ent.usage_summary(),
        "features": [{"key": k, "label": f.label, "help": f.help, "on": ent.has(k)} for k, f in entitlements.FEATURES.items()],
        "ai": user_ai.status(user["id"]),
        "sessions": auth.list_sessions(user["id"], user.get("sid")),
    }


@protected.get("/api/me/usage")
def my_usage(user: dict = Depends(auth.require_user)):
    return entitlements.effective(user["id"]).usage_summary()


@protected.patch("/api/me/profile")
def my_profile(body: ProfileBody, user: dict = Depends(auth.require_user)):
    """Your own display name (email and username are managed by the administrator)."""
    updated = auth.update_user(user["id"], display_name=body.display_name if body.display_name is not None else "")
    return {"user": updated}


@protected.delete("/api/me/sessions/{sid}")
def end_my_session(sid: str, user: dict = Depends(auth.require_user)):
    if sid == user.get("sid"):
        raise ValueError("Use Sign out to end this session.")
    if not auth.end_sessions(user["id"], sid=sid):
        raise LookupError("No such session.")
    return {"ok": True}


@protected.post("/api/me/sessions/revoke-others")
def end_my_other_sessions(request: Request, user: dict = Depends(auth.require_user)):
    token = request.cookies.get(auth.COOKIE)
    n = auth.end_sessions(user["id"], keep_token_hash=auth._token_hash(token) if token else None)
    return {"ended": n}


@protected.get("/api/me/ai")
def my_ai(user: dict = Depends(auth.require_user)):
    """Your AI settings; keys appear only as "saved, ends in 1234"."""
    return user_ai.view(user["id"])


@protected.put("/api/me/ai")
def save_my_ai(body: dict[str, Any], user: dict = Depends(auth.require_user)):
    return user_ai.save(user["id"], body)


@protected.get("/api/me/ai/status")
def my_ai_status(user: dict = Depends(auth.require_user)):
    return user_ai.status(user["id"])


app.include_router(protected)

from . import admin_api  # noqa: E402  (the admin console's routes)

app.include_router(admin_api.router)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


class _RevalidatedStatic(StaticFiles):
    """Scripts and styles are checked with the server on every load (ETag: a short "not
    modified" answer when unchanged), like index.html: after an update the browser never
    runs the new page with old scripts from its cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


app.mount("/static", _RevalidatedStatic(directory=STATIC), name="static")
