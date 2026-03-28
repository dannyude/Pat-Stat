"""Auth endpoints — native DDD implementation."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.rate_limit import limiter
from src.core.redis_client import (
    is_refresh_token_valid,
    revoke_all_user_tokens,
    revoke_refresh_token,
    store_refresh_token,
)
from src.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from src.domains.users.models import DeviceToken, User
from src.domains.users.schemas import (
    ChangePasswordRequest,
    DeviceTokenRegister,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UpdateMeRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.auth_rate_limit)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user and return access/refresh tokens.
    Also handles optional device token registration for push notifications.
    """
    _ = request
    # [DB/Logic]: Find user by email. We lookup first, then verify password
    # to avoid constant time timing attacks (handled by passlib).
    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    # [Token Generation]: Create short-lived access token and long-lived refresh token
    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(user.id)

    # [Security/Redis]: Store the refresh token's JTI in Redis. This allows us
    # to revoke refresh tokens globally by deleting the key.
    refresh_payload = decode_token(refresh_token)
    ttl = int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
    await store_refresh_token(user.id, refresh_payload["jti"], ttl)

    if body.device_token:
        # [Device Tokens]: Upsert the FCM device token if provided during login.
        existing_dt = await db.execute(
            select(DeviceToken).where(DeviceToken.token == body.device_token)
        )
        dt = existing_dt.scalar_one_or_none()
        if dt:
            dt.last_used_at = datetime.now(timezone.utc)
        else:
            db.add(
                DeviceToken(
                    user_id=user.id,
                    token=body.device_token,
                    device_name=body.device_name,
                )
            )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(settings.auth_rate_limit)
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a valid, non-revoked refresh token for a new access+refresh token pair.
    Implements refresh token rotation.
    """
    _ = request
    # [Validation]: Decode and verify it's actually a refresh token
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    user_id = payload["sub"]
    jti = payload["jti"]

    if not await is_refresh_token_valid(user_id, jti):
        raise HTTPException(status_code=401, detail="Refresh token revoked or expired")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # [Security/Redis]: Rotate the tokens. Revoke the old refresh token JTI
    # and issue a completely new pair.
    await revoke_refresh_token(user_id, jti)
    new_access = create_access_token(user.id, user.role.value)
    new_refresh = create_refresh_token(user.id)
    new_payload = decode_token(new_refresh)
    ttl = int(timedelta(days=settings.refresh_token_expire_days).total_seconds())
    await store_refresh_token(user.id, new_payload["jti"], ttl)

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout")
async def logout(
    body: RefreshRequest,
    current_user: User = Depends(get_current_user),
):
    payload = decode_token(body.refresh_token)
    await revoke_refresh_token(current_user.id, payload.get("jti", ""))
    return {"message": "Logged out"}


@router.post("/logout-all")
async def logout_all(current_user: User = Depends(get_current_user)):
    await revoke_all_user_tokens(current_user.id)
    return {"message": "All sessions revoked"}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
):
    if body.full_name is not None:
        current_user.full_name = body.full_name.strip()
    if body.phone is not None:
        current_user.phone = body.phone.strip() or None
    if body.avatar_url is not None:
        current_user.avatar_url = body.avatar_url.strip() or None
    return current_user


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if verify_password(body.new_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="New password must be different")

    current_user.hashed_password = hash_password(body.new_password)
    await revoke_all_user_tokens(current_user.id)
    return {"message": "Password changed. Please log in again."}


@router.post("/device-token")
async def register_device_token(
    body: DeviceTokenRegister,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(DeviceToken).where(DeviceToken.token == body.token)
    )
    dt = existing.scalar_one_or_none()
    if dt:
        dt.last_used_at = datetime.now(timezone.utc)
        dt.user_id = current_user.id
    else:
        db.add(
            DeviceToken(
                user_id=current_user.id,
                token=body.token,
                device_name=body.device_name,
            )
        )
    return {"message": "Device token registered"}


__all__ = ["router"]
