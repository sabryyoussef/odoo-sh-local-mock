"""Map HEAD → GET for HTML routes.

FastAPI's APIRoute sets ``methods`` without adding HEAD (unlike Starlette's
``Route``), so every ``@router.get`` endpoint returns 405 for HEAD. Browsers
and intermediaries (Firefox link probing, health checks, crawlers) send HEAD;
rejecting it breaks loads that work with GET in Chrome.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class HeadAsGetMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "HEAD":
            return await call_next(request)

        request.scope["method"] = "GET"
        response = await call_next(request)
        # HEAD must not include a body; keep status + headers from the GET path.
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-encoding", "transfer-encoding"}
        }
        return Response(content=b"", status_code=response.status_code, headers=headers)
