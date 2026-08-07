import os
from contextlib import asynccontextmanager

from a2wsgi import WSGIMiddleware
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount

from app import app as flask_app
from mcp_server import mcp

API_KEY = os.environ.get("API_KEY")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not API_KEY:
            return await call_next(request)
        provided = request.headers.get("x-api-key", "")
        if not provided:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[len("Bearer "):]
        if provided != API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


mcp_asgi_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_app):
    # FastMCP's streamable-http transport needs its own lifespan running to
    # start its session manager; Starlette doesn't propagate this to a
    # Mount()-ed sub-app automatically, so it's wired up explicitly here.
    async with mcp_asgi_app.router.lifespan_context(mcp_asgi_app):
        yield


app = Starlette(
    routes=[
        Mount("/mcp", app=mcp_asgi_app),
        Mount("/", app=WSGIMiddleware(flask_app)),
    ],
    middleware=[Middleware(ApiKeyMiddleware)],
    lifespan=lifespan,
)
