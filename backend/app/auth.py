"""Signup/login with bcrypt password hashes and JWT access tokens."""
import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

bearer_scheme = HTTPBearer(auto_error=False)


async def hash_password(password: str, rounds: int) -> str:
    # bcrypt is deliberately slow (CPU-bound). Running it in a thread keeps
    # the event loop free to serve other requests meanwhile.
    hashed = await asyncio.to_thread(bcrypt.hashpw, password.encode(), bcrypt.gensalt(rounds))
    return hashed.decode()


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(bcrypt.checkpw, password.encode(), password_hash.encode())


def create_access_token(settings: Settings, user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": str(user_id), "exp": expires}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def optional_user_id(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)
) -> int | None:
    """Like current_user_id, but anonymous visitors get None instead of 401."""
    if credentials is None:
        return None
    return current_user_id(request, credentials)


def current_user_id(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)
) -> int:
    """Dependency: the id of the logged-in user, or 401."""
    settings: Settings = request.app.state.settings
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return int(payload["sub"])
