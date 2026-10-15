"""WebUI and the small JSON API. No accounts: keep the port on the LAN."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from backlist.library import add_series, commit_structure, cover_file, series_browse, series_image, series_pages
from backlist.service import Service

STATIC = Path(__file__).with_name("static")


def _http(exc: ValueError) -> HTTPException:
    text = str(exc)
    code = 404 if text.startswith("unknown series") else 400
    return HTTPException(code, text)


def create_app(appdata: Path | None = None) -> FastAPI:
    service = Service(appdata or Path(os.environ.get("APPDATA_DIR", "/appdata")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service.start()
        try:
            yield
        finally:
            app.state.service.stop()

    app = FastAPI(title="Backlist", lifespan=lifespan)
    app.state.service = service

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/state")
    def state() -> dict:
        return service.state()

    @app.post("/api/scan")
    def scan() -> dict[str, bool]:
        service.request_scan()
        return {"ok": True}

    @app.post("/api/watcher")
    def watcher(body: dict) -> dict:
        if "enabled" not in body:
            raise HTTPException(400, "enabled is required")
        try:
            return service.set_watcher(body["enabled"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/settings")
    def update_settings(body: dict) -> dict:
        try:
            return service.update_settings(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/library")
    def add_library(body: dict) -> dict:
        try:
            return add_series(service.store, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/series")
    def series(series: str) -> dict:
        try:
            return series_browse(service.store, series)
        except ValueError as exc:
            raise _http(exc) from exc

    @app.post("/api/series/structure")
    def series_structure(body: dict) -> dict:
        try:
            return commit_structure(service.store, body)
        except ValueError as exc:
            raise _http(exc) from exc

    @app.get("/api/series/pages")
    def pages(series: str, path: str) -> dict[str, list[str]]:
        try:
            return {"pages": series_pages(service.store, series, path)}
        except ValueError as exc:
            raise _http(exc) from exc

    @app.get("/api/series/image")
    def image(series: str, path: str, page: str | None = None) -> Response:
        try:
            data, media = series_image(service.store, series, path, page)
        except ValueError as exc:
            raise _http(exc) from exc
        return Response(content=data, media_type=media, headers={"Cache-Control": "no-cache"})

    @app.get("/api/cover")
    def cover(series: str) -> FileResponse:
        try:
            path = cover_file(service.store, series)
        except ValueError as exc:
            raise _http(exc) from exc
        if path is None:
            raise HTTPException(404, "no cover")
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    @app.post("/api/import")
    def import_files(body: dict) -> dict[str, str]:
        try:
            action = service.import_files(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"action": action}

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: int) -> dict[str, bool]:
        if not service.retry(job_id):
            raise HTTPException(404, "job cannot be retried")
        return {"ok": True}

    assets = STATIC / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app
