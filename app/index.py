"""Lambda handler using Mangum to adapt FastAPI to Lambda."""

from mangum import Mangum
from main import app

# Mangum wraps the FastAPI ASGI app for Lambda
handler = Mangum(app, lifespan="off")
