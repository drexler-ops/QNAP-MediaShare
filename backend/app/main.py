"""FastAPI application: JSON API under /api, built React app everywhere else.

    uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import admin, auth_routes, decisions, groups, items, media, users
from .config import config
from .deletion.workflow import WorkflowError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("SKIP_INIT_DB") != "1":
        from .db import init_db

        init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=config.app_name,lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        session_cookie="photoaudit_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=False,
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error(_req: Request, exc: WorkflowError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    for module in (auth_routes, users, items, media, groups, decisions, admin):
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
        }

    dist = config.frontend_dist
    if os.path.isdir(os.path.join(dist, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(dist, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = os.path.normpath(os.path.join(dist, full_path))
        if full_path and candidate.startswith(os.path.normpath(dist)) and os.path.isfile(candidate):
            return FileResponse(candidate)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)

    return app


app = create_app()
