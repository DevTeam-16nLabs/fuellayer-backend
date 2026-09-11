from fastapi import APIRouter

from fuellayer.api.v1.integrations import router as integrations_router
from fuellayer.api.v1.me import router as me_router
from fuellayer.api.v1.stores import router as stores_router
from fuellayer.api.v1.system import router as system_router
from fuellayer.modules.diary.router import router as diary_router
from fuellayer.modules.cooking.router import router as cooking_router
from fuellayer.modules.courses.router import router as courses_router
from fuellayer.modules.foods.router import router as foods_router
from fuellayer.modules.kitchen.router import router as kitchen_router
from fuellayer.modules.onboarding.router import router as onboarding_router
from fuellayer.modules.plan.router import portions_router
from fuellayer.modules.plan.router import router as plan_router
from fuellayer.modules.preferences.router import router as preferences_router
from fuellayer.modules.recipe_imports.router import router as recipe_imports_router
from fuellayer.modules.recipes.router import router as recipes_router

api_router = APIRouter()
api_router.include_router(diary_router)
api_router.include_router(preferences_router)
api_router.include_router(cooking_router)
api_router.include_router(plan_router)
api_router.include_router(portions_router)
api_router.include_router(courses_router)
api_router.include_router(system_router, tags=["system"])
api_router.include_router(foods_router)
api_router.include_router(kitchen_router)
api_router.include_router(recipes_router)
api_router.include_router(recipe_imports_router)
api_router.include_router(stores_router, tags=["stores"])
api_router.include_router(onboarding_router, tags=["onboarding"])
api_router.include_router(me_router, tags=["account"])
api_router.include_router(integrations_router, tags=["integrations"])
