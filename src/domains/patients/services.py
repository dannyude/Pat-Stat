"""Patients domain services placeholder for Phase 1."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.patients.enums import NoteCategory
from src.domains.patients.models import PatientProfile
from src.domains.patients.schemas import NoteCategoryCatalogOut, NoteCategoryOptionOut


async def get_patient_by_id(db: AsyncSession, patient_id: str) -> PatientProfile | None:
    # [Performance/UI]: Simple profile lookup for when clinical data isn't needed.
    result = await db.execute(select(PatientProfile).where(PatientProfile.id == patient_id))
    return result.scalar_one_or_none()


def get_note_category_catalog() -> NoteCategoryCatalogOut:
    """Return a UI-ready, data-driven catalog of staff note categories."""
    color_map = {
        NoteCategory.general: "slate",
        NoteCategory.handover: "blue",
        NoteCategory.procedure: "indigo",
        NoteCategory.consultation: "teal",
        NoteCategory.lab_result: "amber",
        NoteCategory.urgent: "red",
    }
    categories = [
        NoteCategoryOptionOut(
            key=category.name,
            label=category.value,
            color=color_map[category],
            sort_order=index,
        )
        for index, category in enumerate(NoteCategory)
    ]
    return NoteCategoryCatalogOut(
        categories=categories,
        supports_urgent_flag=True,
    )
