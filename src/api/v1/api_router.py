"""
Central API Router Hub.

This module aggregates all the individual API routers from src/api/v1/*.
It is then imported by the main application (src/main.py) and mounted
under the /api/v1 prefix.
"""

from fastapi import APIRouter

from src.api.v1.admin import router as admin_router
from src.api.v1.auth import router as auth_router
from src.api.v1.contact_sales import router as contact_sales_router
from src.api.v1.clinical_updates import router as clinical_updates_router
from src.api.v1.dashboard import router as dashboard_router
from src.api.v1.emergency_flags import router as emergency_flags_router
from src.api.v1.family import router as family_router
from src.api.v1.hospitals import router as hospitals_router
from src.api.v1.notifications import router as notifications_router
from src.api.v1.patients import router as patients_router
from src.api.v1.shift_handover import router as shift_handover_router
from src.api.v1.staff_notes import router as staff_notes_router
from src.api.v1.staff_invites import router as staff_invites_router
from src.api.v1.staffs import router as staffs_router
from src.domains.backoffice.router import router as backoffice_router

# [Config]: Initialize the main API v1 router.
# No prefix is set here because the /api/v1 prefix is usually applied in main.py
api_router = APIRouter()

# [Routing]: Register all domain-specific components
api_router.include_router(admin_router)
api_router.include_router(auth_router)
api_router.include_router(backoffice_router)
api_router.include_router(contact_sales_router)
api_router.include_router(clinical_updates_router)
api_router.include_router(dashboard_router)
api_router.include_router(emergency_flags_router)
api_router.include_router(hospitals_router)
api_router.include_router(notifications_router)
api_router.include_router(patients_router)
api_router.include_router(shift_handover_router)
api_router.include_router(staff_notes_router)
api_router.include_router(staff_invites_router)
api_router.include_router(staffs_router)
api_router.include_router(family_router)
