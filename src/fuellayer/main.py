from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )
    return application


app = create_app()
