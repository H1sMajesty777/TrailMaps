import time

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

SETTINGS = Settings(
    _env_file=None, jwt_secret="unit-test-secret-unit-test-secret-1234", bcrypt_rounds=4
)


def test_password_hash_roundtrip_and_salting():
    h1 = hash_password("Secret123", 4)
    h2 = hash_password("Secret123", 4)
    assert h1 != h2 and h1.startswith("$2")
    assert verify_password("Secret123", h1)
    assert not verify_password("secret123", h1)


def test_verify_password_handles_garbage_hash():
    assert verify_password("x", "not-a-bcrypt-hash") is False


def test_token_roundtrip():
    token, payload = create_token(42, "access", SETTINGS)
    decoded = decode_token(token, SETTINGS, "access")
    assert decoded.user_id == 42
    assert decoded.jti == payload.jti
    assert decoded.expires_at - int(time.time()) <= SETTINGS.access_token_ttl_minutes * 60


def test_refresh_ttl_is_30_days_by_default():
    _, payload = create_token(1, "refresh", SETTINGS)
    assert payload.expires_at - int(time.time()) == pytest.approx(30 * 86400, abs=5)


def test_token_type_is_enforced():
    access, _ = create_token(1, "access", SETTINGS)
    refresh, _ = create_token(1, "refresh", SETTINGS)
    with pytest.raises(UnauthorizedError):
        decode_token(access, SETTINGS, "refresh")
    with pytest.raises(UnauthorizedError):
        decode_token(refresh, SETTINGS, "access")


def test_expired_token_has_dedicated_code():
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "jti": "a" * 32,
            "iss": "trailmaps",
            "iat": now - 100,
            "exp": now - 10,
        },
        SETTINGS.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError) as exc:
        decode_token(token, SETTINGS, "access")
    assert exc.value.code == "token_expired"


def test_tampered_wrong_secret_and_none_alg_are_rejected():
    token, _ = create_token(1, "access", SETTINGS)
    with pytest.raises(UnauthorizedError):
        decode_token(token[:-2] + "xx", SETTINGS, "access")
    other = SETTINGS.model_copy(update={"jwt_secret": "y" * 40})
    with pytest.raises(UnauthorizedError):
        decode_token(token, other, "access")
    unsigned = jwt.encode({"sub": "1"}, key=None, algorithm="none")
    with pytest.raises(UnauthorizedError):
        decode_token(unsigned, SETTINGS, "access")


def test_missing_claims_are_rejected():
    token = jwt.encode({"sub": "1", "exp": int(time.time()) + 60}, SETTINGS.jwt_secret, "HS256")
    with pytest.raises(UnauthorizedError):
        decode_token(token, SETTINGS, "access")


def test_non_numeric_subject_is_rejected():
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "abc",
            "type": "access",
            "jti": "j",
            "iss": "trailmaps",
            "iat": now,
            "exp": now + 60,
        },
        SETTINGS.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        decode_token(token, SETTINGS, "access")
