"""Root REST router."""

from fastapi import APIRouter

from app.api.rest.health import router as health_router
from app.modules.auth.router import router as auth_router
from app.modules.recommendations.router import router as recommendations_router

router = APIRouter()
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(recommendations_router)
