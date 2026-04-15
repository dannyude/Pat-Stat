"""Staff invitation model.

Mirrors the FamilyInvite pattern but scoped to hospital staff (doctors & nurses).
An admin creates an invite, the invited staff member receives an email with a
secure link + access code, verifies their identity, and sets up their own password.

Security model:
- The raw URL token is NEVER stored in the database — only a SHA-256 hash.
  If the DB leaks, invite URLs cannot be reconstructed from persisted data.
- An 8-char access code acts as a second factor (out-of-band verification).
- Both factors + email match are required before account creation.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow
from src.domains.users.enums import UserRole


class StaffInvite(Base, UUIDPrimaryKey):
    """One-time invitation for a doctor or nurse to join a hospital."""

    __tablename__ = "staff_invites"

    # [Design]: Every staff invite is scoped to a specific hospital.
    # Unlike family invites (scoped to a patient), staff invites grant
    # hospital-wide access, so the hospital_id is the anchor FK.
    hospital_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "hospitals.id",
            ondelete="CASCADE",
            name="fk_staff_invite_hospital_id",
        ),
        nullable=False,
        index=True,
    )
    inviter_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_staff_invite_inviter_user_id",
        ),
        nullable=True,
        index=True,
    )
    accepted_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_staff_invite_accepted_user_id",
        ),
        nullable=True,
        index=True,
    )

    email = Column(String(255), nullable=False, index=True)
    staff_name = Column(String(255), nullable=False)

    # [Design]: We store the intended role at invite time so the admin's
    # intent is captured. Only doctor/nurse are valid — enforced at the
    # schema validation layer (Pydantic), not at the DB level, because
    # the UserRole enum includes roles that are invalid for staff invites.
    role = Column(SAEnum(UserRole), nullable=False)

    # [Security]: SHA-256 hash of the raw URL token. See module docstring.
    token_hash = Column(String(128), nullable=False, unique=True, index=True)

    # [Security]: Short alphanumeric code (2nd factor).
    # Delivered via a secondary channel or shown to the admin on the success
    # screen so they can share it verbally/via chat with the new staff member.
    access_code = Column(String(20), nullable=False, index=True)

    # Status lifecycle: pending -> accepted | expired | revoked
    status = Column(String(20), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────────────────────
    hospital = relationship("Hospital", foreign_keys=[hospital_id])
    inviter = relationship("User", foreign_keys=[inviter_user_id])
    accepted_user = relationship("User", foreign_keys=[accepted_user_id])

    __table_args__ = (
        # [Performance]: Composite index for the most common admin query:
        # "show me all pending invites for my hospital"
        Index("ix_staff_invites_hospital_status", "hospital_id", "status"),
    )


__all__ = ["StaffInvite"]
