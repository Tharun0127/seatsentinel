"""Signup and login."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas import Credentials, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: Credentials, request: Request):
    settings = request.app.state.settings
    user = User(email=body.email, password_hash=await hash_password(body.password, settings.bcrypt_rounds))
    async with request.app.state.session_factory() as session:
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Rely on the unique index, not a "does it exist?" SELECT first:
            # two simultaneous signups would both pass that check.
            raise HTTPException(409, "email already registered")
    return TokenResponse(access_token=create_access_token(settings, user.id), user_id=user.id, email=user.email)


@router.post("/login", response_model=TokenResponse)
async def login(body: Credentials, request: Request):
    settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == body.email))
    if user is None or not await verify_password(body.password, user.password_hash):
        raise HTTPException(401, "wrong email or password")
    return TokenResponse(access_token=create_access_token(settings, user.id), user_id=user.id, email=user.email)
