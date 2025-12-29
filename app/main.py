"""Claudepedia - A persistent knowledge base for Claude instances."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import router as api_router
from web import router as web_router
from db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "claudepedia"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
