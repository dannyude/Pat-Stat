"""Integration tests for the backoffice hospital documents endpoint.

Covers: GET /backoffice/hospitals/{hospital_id}/documents
"""

from httpx import AsyncClient
import pytest

from src.domains.backoffice.models.onboarding import (
    DocumentType,
    HospitalApplication,
    HospitalDocument,
)
from src.domains.users.enums import UserRole
from tests.helpers import login_for_token, seed_hospital, seed_user, unique_email

pytestmark = pytest.mark.asyncio


async def _super_admin_token(api_client, db_session, prefix="docs-sa"):
    email = unique_email(prefix)
    await seed_user(
        db_session, email=email, role=UserRole.super_admin, full_name="Super Admin"
    )
    return await login_for_token(api_client, email)


class TestBackofficeDocuments:
    async def test_returns_empty_list_when_hospital_has_no_application(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "No Application Hospital")
        token = await _super_admin_token(api_client, db_session, "docs-no-app")

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_empty_list_when_application_has_no_documents(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "No Docs Hospital")
        application = HospitalApplication(
            hospital_id=hospital.id,
            admin_full_name="Dr. Admin",
            admin_email=unique_email("no-docs-admin"),
        )
        db_session.add(application)
        await db_session.commit()

        token = await _super_admin_token(api_client, db_session, "docs-no-docs")

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_uploaded_documents(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Has Docs Hospital")
        application = HospitalApplication(
            hospital_id=hospital.id,
            admin_full_name="Dr. With Docs",
            admin_email=unique_email("has-docs-admin"),
        )
        db_session.add(application)
        await db_session.flush()

        db_session.add(
            HospitalDocument(
                application_id=str(application.id),
                document_type=DocumentType.cac_certificate,
                file_name="cac_cert.pdf",
                file_url="https://cdn.example.com/cac_cert.pdf",
                file_size_bytes=102400,
                mime_type="application/pdf",
            )
        )
        await db_session.commit()

        token = await _super_admin_token(api_client, db_session, "docs-with-docs")

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) == 1
        assert docs[0]["file_name"] == "cac_cert.pdf"
        assert docs[0]["document_type"] == "CAC Certificate"
        assert docs[0]["mime_type"] == "application/pdf"
        assert docs[0]["file_size_bytes"] == 102400

    async def test_returns_all_documents_for_application(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Multi Docs Hospital")
        application = HospitalApplication(
            hospital_id=hospital.id,
            admin_full_name="Dr. Multi",
            admin_email=unique_email("multi-docs-admin"),
        )
        db_session.add(application)
        await db_session.flush()

        for doc_type, fname in [
            (DocumentType.cac_certificate, "cac.pdf"),
            (DocumentType.medical_license, "license.pdf"),
            (DocumentType.tax_clearance, "tax.pdf"),
        ]:
            db_session.add(
                HospitalDocument(
                    application_id=str(application.id),
                    document_type=doc_type,
                    file_name=fname,
                    file_url=f"https://cdn.example.com/{fname}",
                )
            )
        await db_session.commit()

        token = await _super_admin_token(api_client, db_session, "docs-multi")

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    async def test_requires_super_admin_role(
        self, api_client: AsyncClient, db_session
    ):
        hospital = await seed_hospital(db_session, "Docs Auth Hospital")
        admin_email = unique_email("docs-regular-admin")
        await seed_user(
            db_session,
            email=admin_email,
            role=UserRole.admin,
            hospital_id=hospital.id,
        )
        token = await login_for_token(api_client, admin_email)

        resp = await api_client.get(
            f"/api/v1/backoffice/hospitals/{hospital.id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
