"""FastAPI application. Caddy serves the built frontend and proxies /api here."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import links as links_api
from .api import router
from .api.v1 import router as v1_router

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

app = FastAPI(title="RHIS Mailfilter", version=__version__, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # The session cookie is SameSite=Strict; on top of that every state-changing
    # request must carry a custom header, which other sites cannot send.
    # The public API (/api/v1) ignores cookies and needs an API key instead.
    path = request.url.path
    if request.method not in SAFE_METHODS and path.startswith("/api/") and not path.startswith("/api/v1/"):
        if request.headers.get("x-requested-with") != "mailfilter":
            return JSONResponse({"detail": "csrf"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/api/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


app.include_router(router)
# Click links of link protection: https://<host>/l/<token>, outside /api.
app.include_router(links_api.public)
# The public API has its own OpenAPI description (/api/v1/openapi.json).
app.include_router(v1_router, include_in_schema=False)
