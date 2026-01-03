"""Lambda handler using Mangum to adapt FastAPI to Lambda."""

from mangum import Mangum
from main import app

# Mangum wraps the FastAPI ASGI app for Lambda
# lifespan="auto" allows the MCP session manager to initialize
handler = Mangum(app, lifespan="auto")
