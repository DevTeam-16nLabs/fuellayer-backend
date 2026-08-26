from fastapi import FastAPI

from fuellayer.api.v1.router import api_router
from fuellayer.core.config import settings


def create_app() -> FastAPI:
    application = FastAPI(
        title="FuelLayer API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
