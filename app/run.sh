#!/bin/bash
# Lambda Web Adapter entrypoint
# Runs uvicorn with FastAPI

exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
