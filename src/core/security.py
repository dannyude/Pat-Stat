from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.domains.users.models import User, UserRole

# [Security]: Password Hashing Configuration
# Bcrypt is the industry standard for secure one-way hash algorithms.
# 'deprecated="auto"' ensures that if bcrypt is ever upgraded to a newer hash format,
# old passwords get cleanly upgraded on next login.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# [Architecture]: FastAPI Dependency for extracting the Bearer token from the Auth header
bearer_scheme = HTTPBearer()


def hash_password(plain: str) -> str:
    """
    [Security]: Hashes a plaintext password using the configured password context.
    """
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    [Security]: Verifies a plaintext submission against the securely stored bcrypt hash.
    It automatically handles salt extraction and timing-attack protections.
    """
    return pwd_context.verify(plain, hashed)


def _create_token(data: dict, expires_delta: timedelta) -> str:
    """
    [Logic]: Internal helper to generate a signed JSON Web Token.
    Includes an explicit expiration claim ('exp') and a unique token ID ('jti') 
    which could be used later for precise token revocation if needed.
    """
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, role: str) -> str:
    """
    [Security]: Creates a short-lived access token carrying identity claims.
    This is what the client places in the 'Authorization: Bearer' header for every API call.
    """
    return _create_token(
        {"sub": user_id, "role": role, "type": "access"},
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: str) -> str:
    """
    [Security]: Creates a long-lived refresh token.
    This token exists ONLY to attain a new access token when the old one expires,
    preventing the user from having to log in manually again.
    """
    return _create_token(
        {"sub": user_id, "type": "refresh"},
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> dict:
    """
    [Security]: Decodes a JWT, implicitly validating its cryptographic signature and expiration.
    If the token has been tampered with or has expired, jose immediately raises an error.
    """
    try:
        return jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    [Architecture/Security]: The Primary Auth Guard.
    Used as a FastAPI dependency injected into protected routes.
    1. Extracts and validates the token.
    2. Enforces that it is an 'access' token (not 'refresh').
    3. Fetches the User from the database to ensure they haven't been deleted.
    4. Enforces the User is currently 'active' (e.g. Hospital hasn't been suspended).
    """
    from sqlalchemy.orm import selectinload

    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    
    # [Performance]: Eager load the user's hospital since it is heavily accessed 
    # immediately after login for scoping data.
    result = await db.execute(
        select(User)
        .options(selectinload(User.hospital))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


def require_roles(*roles: UserRole):
    """
    [Architecture/Security]: Role-Based Access Control (RBAC) Guard Factory.
    Generates tailored dependencies to enforce that the `current_user` has
    a specific role required to execute an action.
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}",
            )
        return current_user

    return _check


# [Architecture]: Pre-computed RBAC dependencies injected safely across routers.
require_super_admin = require_roles(UserRole.super_admin)
require_admin = require_roles(UserRole.admin)
require_clinical = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)
require_doctor = require_roles(UserRole.admin, UserRole.doctor)


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "require_roles",
    "require_super_admin",
    "require_admin",
    "require_clinical",
    "require_doctor",
]

