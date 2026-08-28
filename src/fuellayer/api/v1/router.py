from fastapi import APIRouter

from fuellayer.api.v1.integrations import router as integrations_router
from fuellayer.api.v1.me import router as me_router
from fuellayer.api.v1.system import router as system_router
from fuellayer.modules.onboarding.router import router as onboarding_router

api_router = APIRouter()
api_router.include_router(system_router, tags=["system"])
api_router.include_router(onboarding_router, tags=["onboarding"])
api_router.include_router(me_router, tags=["account"])
api_router.include_router(integrations_router, tags=["integrations"])
