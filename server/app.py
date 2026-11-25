"""FastAPI 入口。"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.api import build_api
from server.config import load_config
from server.runtime import Runtime, build_runtime, start_runtime, stop_runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartcctv")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_CANDIDATES = [ROOT / "config.yaml", ROOT / "config.example.yaml"]

_runtime: Runtime | None = None


def get_runtime() -> Runtime | None:
    return _runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runtime
    config_path = next((p for p in CONFIG_CANDIDATES if p.exists()), CONFIG_CANDIDATES[0])
    if not config_path.exists():
        raise RuntimeError("缺少 config.yaml / config.example.yaml")
    config = load_config(config_path)
    _runtime = build_runtime(ROOT, config_path, config)
    await start_runtime(_runtime)
    log.info("SmartCCTV listening intent %s:%s", config.host, config.port)
    try:
        yield
    finally:
        if _runtime:
            await stop_runtime(_runtime)
            _runtime = None


app = FastAPI(title="SmartCCTV", lifespan=lifespan)
app.include_router(build_api(get_runtime))


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    runtime = get_runtime()
    ip = request.client.host if request.client else "unknown"
    if runtime and not runtime.limiter.allow(ip):
        return JSONResponse({"detail": "请求过于频繁"}, status_code=429)
    path = request.url.path
    public = path.startswith("/api/auth/") or path in {"/login", "/"} or path.startswith("/assets/")
    # 总控 HMAC 口：免 cookie，路由内自校验签名
    hmac_paths = path.startswith("/api/internal/") or path in {
        "/api/passages/start",
        "/api/passages/stop",
    } or path.startswith("/api/passages/by-command/")
    if path.startswith("/api/") and not path.startswith("/api/auth/") and not hmac_paths and runtime:
        token = request.cookies.get("session", "")
        if runtime.auth.read_token(token) is None and path not in {"/api/auth/login"}:
            # MJPEG 与其它 API 统一要登录；静态除外
            if not public:
                return JSONResponse({"detail": "未登录"}, status_code=401)
    response: Response = await call_next(request)
    if path.startswith("/api/cameras/") and path.endswith("/mjpeg"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(dist / "index.html")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
else:

    @app.get("/")
    async def index_fallback():
        return {
            "name": "SmartCCTV",
            "hint": "前端尚未构建。开发可用 frontend npm run dev；生产请 npm run build。",
            "ts": time.time(),
        }
