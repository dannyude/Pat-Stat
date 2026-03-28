"""Compatibility aggregator for family route modules.

This file keeps the existing import path (`src.api.v1.family`) stable while the
actual endpoints are split across dedicated route modules.
"""

from fastapi import APIRouter

from src.api.v1.family_access import router as family_access_router
from src.api.v1.family_dashboard import router as family_dashboard_router
from src.api.v1.family_invites import router as family_invites_router

router = APIRouter()
router.include_router(family_access_router)
router.include_router(family_invites_router)
router.include_router(family_dashboard_router)

__all__ = ["router"]
