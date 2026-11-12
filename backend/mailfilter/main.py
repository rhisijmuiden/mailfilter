"""FastAPI application. Caddy serves the built frontend and proxies /api here."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import links as links_api
from .api import router

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

app = FastAPI(title="RHIS Mailfilter", version=__version__, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    # The session cookie is SameSite=Strict; on top of that every state-changing
    # request must carry a custom header, which other sites cannot send.
    if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
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
