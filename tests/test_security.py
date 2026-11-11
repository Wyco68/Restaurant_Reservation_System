"""Password hashing past bcrypt's 72-byte limit (app/security.py).

Signup allows 128 characters, up to 512 bytes. bcrypt 4 ignored everything
after byte 72; bcrypt 5 raises instead. These tests fail on bcrypt 5 if the
password is not cut to 72 bytes before hashing and checking.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.security import BCRYPT_MAX_BYTES, hash_password, verify_password

LONG = "a" * 100

# bcrypt 4.2.1 hash of LONG, as stored before the upgrade.
BCRYPT_4_HASH = "$2b$04$b/R/vF5JCqENxOdAWbIOouV4Vo/ag8kvdvLLLQCjtdO5vLgIXIWai"

# 30 Burmese characters: 30 characters, 90 bytes.
BURMESE = "က" * 30


async def test_long_password_round_trip():
    assert len(LONG.encode()) > BCRYPT_MAX_BYTES
    hashed = await hash_password(LONG)
    assert await verify_password(LONG, hashed)
    assert not await verify_password("b" * 100, hashed)


async def test_hash_from_bcrypt_4_still_verifies():
    assert await verify_password(LONG, BCRYPT_4_HASH)
    assert not await verify_password("b" * 100, BCRYPT_4_HASH)


async def test_signup_and_login_with_long_password(client: AsyncClient, unique_email: str):
    assert len(BURMESE.encode()) > BCRYPT_MAX_BYTES
    r = await client.post(
        "/api/v1/users",
        json={"email": unique_email, "password": BURMESE, "full_name": "Long Password"},
    )
    assert r.status_code == 201
    r = await client.post("/api/v1/auth/login", json={"email": unique_email, "password": BURMESE})
    assert r.status_code == 200
    assert r.json()["access_token"]
