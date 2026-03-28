"""Shared helper utilities for integration tests."""

import uuid

from httpx import AsyncClient

from src.core.database import AsyncSessionLocal
from src.domains.hospital.models import Hospital
from src.domains.patients.models import Admission, FamilyPatientLink, PatientProfile
from src.domains.users.enums import UserRole
from src.domains.users.models import User


async def seed_hospital(session, name: str = "Test Hospital") -> Hospital:
    hospital = Hospital(
        name=name,
        address={"street": "1 Test St", "city": "Test City"},
        phone="0000000000",
        email=f"hospital-{uuid.uuid4().hex[:6]}@test.com",
        hospital_code=f"H-{uuid.uuid4().hex[:6].upper()}",
        subdomain=f"test-{uuid.uuid4().hex[:8]}",
        state="Test State",
        status="active",
    )
    session.add(hospital)
    await session.flush()
    await session.commit()
    return hospital


async def seed_user(
    session,
    email: str,
    role: UserRole,
    full_name: str = "Test User",
    password: str = "Password123",
    hospital_id=None,
) -> User:
    # Keep seeded credentials consistent with auth monkeypatch behavior.
    user = User(
        email=email,
        hashed_password=f"test-hash::{password}",
        full_name=full_name,
        role=role,
        hospital_id=hospital_id,
    )
    session.add(user)
    await session.flush()
    await session.commit()
    return user


async def seed_admission(
    session,
    hospital_id,
    full_name: str = "Test Patient",
    ward: str = "General Ward A",
    bed_number: str = "A-01",
    diagnosis: str = "Test Diagnosis",
    status: str = "Stable",
    primary_doctor_id=None,
):
    """Create a patient profile + active admission and return (patient, admission)."""
    from src.domains.patients.enums import PatientStatus

    patient = PatientProfile(
        pat_stat_id=f"PS-{uuid.uuid4().hex[:8].upper()}",
        full_name=full_name,
        gender="male",
    )
    session.add(patient)
    await session.flush()

    status_enum = PatientStatus(status)
    admission = Admission(
        patient_id=patient.id,
        hospital_id=hospital_id,
        ward=ward,
        bed_number=bed_number,
        diagnosis=diagnosis,
        status=status_enum,
        primary_doctor_id=primary_doctor_id,
    )
    session.add(admission)
    await session.flush()
    await session.commit()
    return patient, admission


async def seed_family_link(
    patient_id,
    family_user_id,
    relationship_to_patient: str | None = None,
) -> FamilyPatientLink:
    """Seed a FamilyPatientLink using a dedicated session.

    Uses its own AsyncSessionLocal (same pattern as other seed helpers) so the
    committed patient/user rows are visible before the FK check runs.
    """
    async with AsyncSessionLocal() as session:
        link = FamilyPatientLink(
            patient_id=str(patient_id),
            family_user_id=str(family_user_id),
            relationship_to_patient=relationship_to_patient,
        )
        session.add(link)
        await session.flush()
        await session.commit()
        return link


def unique_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


async def login_for_token(
    api_client: AsyncClient,
    email: str,
    password: str = "Password123",
) -> str:
    login_resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200
    return login_resp.json()["access_token"]

