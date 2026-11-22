"""FastAPI application: JSON API under /api, built React app everywhere else.

    uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import admin, albums, auth_routes, decisions, groups, items, media, public, users
from .albums import AlbumError
from .config import config
from .deletion.workflow import WorkflowError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("SKIP_INIT_DB") != "1":
        from .db import init_db

        init_db()
    yield


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Other sites only ever learn our origin, never a path (so share tokens in /s/<token> can't leak).
    # Must not be "no-referrer" in the main app: OpenStreetMap's tile policy requires a Referer and
    # blocks tiles without one.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# The internet-facing share-only app loads nothing from other sites, so it sends no Referer at all.
SHARE_ONLY_HEADERS = {**SECURITY_HEADERS, "Referrer-Policy": "no-referrer"}


def create_app(share_only: bool | None = None) -> FastAPI:
    """share_only=True builds the internet-facing variant: public album links only, no sign-in, no API."""
    share_only = config.share_only if share_only is None else share_only
    app = FastAPI(
        title=config.app_name,
        lifespan=lifespan,
        docs_url=None if share_only else "/api/docs",
        openapi_url=None if share_only else "/api/openapi.json",
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        session_cookie="photoaudit_share" if share_only else "photoaudit_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=False,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in (SHARE_ONLY_HEADERS if share_only else SECURITY_HEADERS).items():
            response.headers.setdefault(k, v)
        return response

    @app.exception_handler(WorkflowError)
    async def workflow_error(_req: Request, exc: WorkflowError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(AlbumError)
    async def album_error(_req: Request, exc: AlbumError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    modules = (public,) if share_only else (auth_routes, users, items, media, groups, decisions, admin, albums, public)
    for module in modules:
        app.include_router(module.router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def public_config():
        """Deployment-specific display settings (no secrets); needed before sign-in."""
        tiles = config.map_tile_url
        return {
            "app_name": config.app_name,
            "map_tile_url": None if tiles.lower() in ("off", "none", "disabled") else tiles,
            "map_attribution": config.map_attribution,
            "share_only": share_only,
            "share_base_url": config.public_share_url or None,
        }

    dist = config.frontend_dist
    if os.path.isdir(os.path.join(dist, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if share_only and not (full_path.startswith("s/") or full_path in ("favicon.ico", "robots.txt")):
            # The public container only shows share links; everything else simply doesn't exist here.
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if full_path == "robots.txt":
            return PlainTextResponse("User-agent: *" + chr(10) + "Disallow: /" + chr(10))
        candidate = os.path.normpath(os.path.join(dist, full_path))
        if full_path and candidate.startswith(os.path.normpath(dist)) and os.path.isfile(candidate):
            return FileResponse(candidate)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)

    return app


app = create_app()
