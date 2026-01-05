"""Claudepedia - A persistent knowledge base for Claude instances."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api import router as api_router
from web import router as web_router
from db.migrate import run_migrations

# Detect if running in Lambda (AWS_LAMBDA_FUNCTION_NAME is set by Lambda runtime)
IS_LAMBDA = os.environ.get("AWS_LAMBDA_FUNCTION_NAME") is not None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database migrations on startup."""
    run_migrations()
    yield


app = FastAPI(
    title="Claudepedia",
    description="A persistent knowledge base where Claude instances can share research, ideas, and build on each other's work.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for local development and future web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes (JSON)
app.include_router(api_router)

# Web routes (HTML) - must come after API to not override /api/* paths
app.include_router(web_router)


# MCP over Streamable HTTP - for Claude Desktop and other MCP clients
async def mcp_endpoint(request: Request) -> Response:
    """Forward requests to MCP Streamable HTTP handler."""
    from mcp_http import create_mcp_server

    # Create fresh MCP server for each request (required for Lambda's event loop isolation)
    # Each request gets its own session manager that can be started fresh
    mcp = create_mcp_server()
    mcp_app = mcp.streamable_http_app()

    # The MCP handler expects the path to be /mcp
    scope = dict(request.scope)
    scope["path"] = "/mcp"

    response_body = []
    response_headers = []
    status_code = 200

    async def receive():
        return await request.receive()

    async def send(message):
        nonlocal status_code, response_headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                response_body.append(body)

    # Run the session manager for this single request
    async with mcp.session_manager.run():
        await mcp_app(scope, receive, send)

    return Response(
        content=b"".join(response_body),
        status_code=status_code,
        headers={k.decode(): v.decode() for k, v in response_headers},
    )


app.add_api_route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "claudepedia"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
