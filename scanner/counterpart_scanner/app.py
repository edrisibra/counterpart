"""The scanner web app.

Routes:
    GET  /                     the form
    POST /scan                 validate, scan, redirect to the report
    GET  /report/{id}          the report page
    GET  /badge/{id}.svg       an embeddable badge
    GET  /api/scan?url=...     the same scan as JSON, for CI
    GET  /healthz              liveness

Run it with::

    uvicorn counterpart_scanner.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from counterpart_scanner import render
from counterpart_scanner.guard import UrlRejected, admit
from counterpart_scanner.scan import Store, run_scan

# One scan is a burst of requests at somebody else's server, so the ceiling is low.
RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 300
MAX_CONCURRENT_SCANS = 4

_store = Store()
_hits: defaultdict[str, deque[float]] = defaultdict(deque)
_scan_slots = asyncio.Semaphore(MAX_CONCURRENT_SCANS)


def _client_ip(request: Request) -> str:
    # Only trust a forwarded header if the deployment sets one; behind a proxy the
    # left-most entry is the client. Falls back to the socket address.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    seen = _hits[ip]
    while seen and seen[0] < now - RATE_WINDOW_SECONDS:
        seen.popleft()
    if len(seen) >= RATE_LIMIT:
        return True
    seen.append(now)
    return False


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


async def home(request: Request) -> Response:
    return HTMLResponse(render.home())


async def scan(request: Request) -> Response:
    form = await request.form()
    raw = str(form.get("url") or "")
    ip = _client_ip(request)

    if _rate_limited(ip):
        return HTMLResponse(
            render.home("that is a lot of scans. Try again in a few minutes.", raw), status_code=429
        )
    try:
        target = admit(raw)
    except UrlRejected as exc:
        return HTMLResponse(render.home(str(exc), raw), status_code=400)

    async with _scan_slots:
        result = await run_scan(target)
    _store.put(result)
    return RedirectResponse(f"/report/{result.id}", status_code=303)


async def report(request: Request) -> Response:
    found = _store.get(request.path_params["scan_id"])
    if found is None:
        return HTMLResponse(
            render.home("that result has expired. Scans are kept for a day."), status_code=404
        )
    return HTMLResponse(render.report(found, _base(request)))


async def badge(request: Request) -> Response:
    found = _store.get(request.path_params["scan_id"])
    if found is None:
        return Response("not found", status_code=404, media_type="text/plain")
    return Response(
        render.badge(found),
        media_type="image/svg+xml",
        headers={"cache-control": "max-age=300, public"},
    )


async def api_scan(request: Request) -> Response:
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse({"error": "rate limited"}, status_code=429)
    try:
        target = admit(request.query_params.get("url", ""))
    except UrlRejected as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    async with _scan_slots:
        result = await run_scan(target)
    _store.put(result)
    payload = result.as_dict()
    payload["report_url"] = f"{_base(request)}/report/{result.id}"
    payload["badge_url"] = f"{_base(request)}/badge/{result.id}.svg"
    return JSONResponse(payload)


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True})


_SECURITY_HEADERS = {
    # The pages are self-contained, so the policy can be this tight.
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; base-uri 'none'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


class SecurityHeaders:
    def __init__(self, app: Starlette) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for key, value in _SECURITY_HEADERS.items():
                    headers.append((key.encode(), value.encode()))
            await send(message)

        await self.app(scope, receive, _send)


app: Starlette = Starlette(
    routes=[
        Route("/", home),
        Route("/scan", scan, methods=["POST"]),
        Route("/report/{scan_id}", report),
        Route("/badge/{scan_id}.svg", badge),
        Route("/api/scan", api_scan),
        Route("/healthz", healthz),
    ]
)
app = SecurityHeaders(app)  # type: ignore[assignment]
