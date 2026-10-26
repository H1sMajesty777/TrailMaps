"""Password hashing (bcrypt) and JWT helpers."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal

import bcrypt
import jwt

from app.core.config import Settings
from app.core.errors import UnauthorizedError

TokenType = Literal["access", "refresh"]


def hash_password(password: str, rounds: int) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


async def hash_password_async(password: str, rounds: int) -> str:
    return await asyncio.to_thread(hash_password, password, rounds)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


@lru_cache
def _dummy_hash(rounds: int) -> str:
    return hash_password("dummy-password-for-timing-equalisation", rounds)


def _burn(password: str, rounds: int) -> None:
    verify_password(password, _dummy_hash(rounds))


async def burn_password_check(password: str, rounds: int) -> None:
    """Spend the same CPU time as a real check so unknown e-mails are not distinguishable."""
    await asyncio.to_thread(_burn, password, rounds)


@dataclass(frozen=True, slots=True)
class TokenPayload:
    user_id: int
    jti: str
    token_type: str
    expires_at: int


def create_token(
    user_id: int, token_type: TokenType, settings: Settings
) -> tuple[str, TokenPayload]:
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    expires = int((now + ttl).timestamp())
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user_id),
        "type": token_type,
        "jti": jti,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": expires,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, TokenPayload(user_id=user_id, jti=jti, token_type=token_type, expires_at=expires)


def decode_token(token: str, settings: Settings, expected_type: TokenType) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti", "type", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid token", code="invalid_token") from exc
    if claims.get("type") != expected_type:
        raise UnauthorizedError("Wrong token type", code="invalid_token")
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("Invalid token", code="invalid_token") from exc
    return TokenPayload(
        user_id=user_id,
        jti=str(claims["jti"]),
        token_type=expected_type,
        expires_at=int(claims["exp"]),
    )
