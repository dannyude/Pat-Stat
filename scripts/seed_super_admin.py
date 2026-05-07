"""Bootstrap-only super-admin seeder for PatStat.

This script creates the FIRST super-admin account when the platform has none.
All subsequent super-admin accounts MUST be created through the authenticated
API endpoint: POST /api/v1/backoffice/super-admins

Usage (first deployment only):

    python scripts/seed_super_admin.py \\
        --email admin@patstat.io \\
        --password "<your-password-here>" \\
        --full-name "Platform Admin"

Credentials can also be supplied via environment variables so that passwords
never appear in shell history or CI logs:

    SEED_EMAIL=admin@patstat.io \\
    SEED_PASSWORD=<your-password-here> \\
    SEED_FULL_NAME="Platform Admin" \\
    python scripts/seed_super_admin.py

Exit codes
----------
0  — super-admin already exists with this email (no-op) OR was created.
1  — validation error (bad email, weak password, missing args).
2  — a super-admin already exists — use the API to create more.
3  — unexpected runtime error (see stderr).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# Ensure project root is on sys.path regardless of invocation directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.sql.functions import count  # noqa: E402

from src.domains.backoffice.services import MAX_SUPER_ADMINS  # noqa: E402
from src.core.database import AsyncSessionLocal, engine  # noqa: E402
from src.core.security import hash_password  # noqa: E402
from src.domains.users.enums import UserRole  # noqa: E402
from src.domains.users.models import User  # noqa: E402
from src.domains.hospital.models import Hospital  # noqa: E402
from src.domains.assignments.models import CareAssignment # noqa: E402
from src.domains.family.models import FamilyPatientLink # noqa: E402

# Healthcare-grade password policy for privileged bootstrap accounts.
# Stricter than the general app minimum (8 chars) because super-admins carry
# platform-wide authority and are never scoped to a single hospital.
_MIN_PASSWORD_LENGTH: int = 12
_PASSWORD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Z]"), "at least one uppercase letter"),
    (re.compile(r"[a-z]"), "at least one lowercase letter"),
    (re.compile(r"\d"), "at least one digit"),
    (
        re.compile(r"""[!@#$%^&*()\-_=+\[\]{};':",.<>/?`~\\|]"""),
        "at least one special character",
    ),
]

# RFC 5322-inspired check — catches obvious typos before hitting the DB.
_EMAIL_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


# Value object — validates all inputs before they touch the database layer.
# Keeps validation logic in one place and separate from I/O concerns.
class _SeedRequest:
    """Immutable, self-validating seed request.

    Raises ``ValueError`` with a human-readable message on any constraint
    violation, so the CLI can surface it cleanly without a traceback.
    """

    __slots__ = ("email", "password", "full_name")

    def __init__(self, email: str, password: str, full_name: str) -> None:
        self.email: str = self._validate_email(email)
        self.password: str = self._validate_password(password)
        self.full_name: str = self._validate_full_name(full_name)

    @staticmethod
    def _validate_email(raw: str) -> str:
        normalised = raw.strip().lower()
        if not _EMAIL_RE.match(normalised):
            raise ValueError(
                f"'{raw}' is not a valid email address. "
                "Expected format: user@domain.tld"
            )
        return normalised

    @staticmethod
    def _validate_password(raw: str) -> str:
        errors: list[str] = []
        if len(raw) < _MIN_PASSWORD_LENGTH:
            errors.append(
                f"minimum length is {_MIN_PASSWORD_LENGTH} characters "
                f"(got {len(raw)})"
            )
        for pattern, description in _PASSWORD_RULES:
            if not pattern.search(raw):
                errors.append(description)
        if errors:
            bullet = "\n  • ".join(errors)
            raise ValueError(
                f"Password does not meet the healthcare-grade policy:\n  • {bullet}"
            )
        return raw

    @staticmethod
    def _validate_full_name(raw: str) -> str:
        normalised = raw.strip()
        if len(normalised) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if len(normalised) > 255:
            raise ValueError("Full name must be 255 characters or fewer.")
        return normalised


# Seed use-case
async def _seed(request: _SeedRequest) -> int:
    """Create the FIRST bootstrap super-admin.

    Bootstrap-only guard
    --------------------
    This script ONLY works when zero super-admins exist in the database.
    Once the first super-admin is bootstrapped, all subsequent accounts
    must be created through the authenticated API endpoint:
        POST /api/v1/backoffice/super-admins

    Idempotency guarantee
    ---------------------
    • Same email already exists  → silent no-op, exit 0.
    • Any super-admin exists     → informative message, exit 2.
    • Concurrent insert race     → IntegrityError on users.email unique
                                   constraint is caught and treated as no-op.

    Returns an exit code so the caller (main) can sys.exit() *after* asyncio
    has finished cleanly — avoiding ResourceWarning from abrupt loop teardown.
    """

    
    async with AsyncSessionLocal() as db:
        # ── 1. Bootstrap guard — only works when platform has zero super-admins
        seat_count: int = (
            await db.execute(
                select(count())
                .select_from(User)
                .where(User.role == UserRole.super_admin)
            )
        ).scalar_one()

        if seat_count > 0:
            print(
                f"[seed] {seat_count} super-admin(s) already exist on platform.\n"
                "       This script is for FIRST-TIME BOOTSTRAP only.\n"
                "       To add more super-admins, use the API:\n"
                "         POST /api/v1/backoffice/super-admins\n"
                "       (requires authentication as an existing super-admin)",
                file=sys.stderr,
            )
            return 2

        # ── 2. Optimistic insert (DB unique constraint is the safety net) ────
        user = User(
            email=request.email,
            hashed_password=hash_password(request.password),
            full_name=request.full_name,
            role=UserRole.super_admin,
            is_active=True,
            hospital_id=None,
        )
        db.add(user)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            print(
                f"[seed] super-admin with email '{request.email}' already exists "
                "(detected via unique constraint) — skipping."
            )
            return 0

        print(
            f"[seed] ✓ Bootstrap super-admin '{request.email}' created "
            f"(id={user.id}).\n"
            f"       You can now log in and create up to "
            f"{MAX_SUPER_ADMINS - 1} more via the API."
        )
        return 0


# CLI
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap the FIRST PatStat super-admin (one-time setup).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Password policy (healthcare-grade):\n"
            f"  • Minimum {_MIN_PASSWORD_LENGTH} characters\n"
            + "".join(f"  • {desc}\n" for _, desc in _PASSWORD_RULES)
        ),
    )
    parser.add_argument(
        "--email",
        default=os.getenv("SEED_EMAIL"),
        help="Super-admin email address  (env: SEED_EMAIL)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("SEED_PASSWORD"),
        help=(
            "Plaintext password — bcrypt-hashed before storage  " "(env: SEED_PASSWORD)"
        ),
    )
    parser.add_argument(
        "--full-name",
        dest="full_name",
        default=os.getenv("SEED_FULL_NAME", "Platform Admin"),
        help="Display name  (env: SEED_FULL_NAME, default: 'Platform Admin')",
    )
    return parser.parse_args()


async def _run_seed_and_cleanup(request: _SeedRequest) -> int:
    """Runs the seed task and guarantees DB pool cleanup in the SAME loop."""
    try:
        return await _seed(request)
    finally:
        await engine.dispose()


def main() -> None:
    args = _parse_args()

    # Surface missing required args before touching the DB or hashing anything.
    missing = [
        flag
        for flag, val in [("--email", args.email), ("--password", args.password)]
        if not val
    ]
    if missing:
        print(
            f"[seed] missing required arguments: {', '.join(missing)}\n"
            "       Pass them as flags or set SEED_EMAIL / SEED_PASSWORD env vars.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate inputs before opening a DB connection.
    try:
        request = _SeedRequest(
            email=args.email,
            password=args.password,
            full_name=args.full_name,
        )
    except ValueError as exc:
        print(f"[seed] validation error:\n{exc}", file=sys.stderr)
        sys.exit(1)

    try:
        # Run everything inside a single event loop!
        exit_code = asyncio.run(_run_seed_and_cleanup(request))
    except Exception as exc:
        print(f"[seed] unexpected error: {exc}", file=sys.stderr)
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
