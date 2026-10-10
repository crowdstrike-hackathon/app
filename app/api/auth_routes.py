from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    create_access_token,
    get_current_user,
    get_user_by_email,
    hash_password,
    verify_password,
)
from ..db import get_db
from ..orm import User

router = APIRouter(prefix="/api/auth")


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    name: str = ""
    org: str = ""


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def _user_public(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "org": u.org}


@router.post("/register", response_model=TokenOut)
async def register(body: RegisterBody, db: AsyncSession = Depends(get_db)) -> TokenOut:
    existing = await get_user_by_email(db, body.email)
    if existing:
        raise HTTPException(409, "An account with that email already exists")
    user = User(
        email=body.email,
        name=body.name or body.email.split("@")[0],
        org=body.org,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenOut(access_token=create_access_token(user.id), user=_user_public(user))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)) -> TokenOut:
    user = await get_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return TokenOut(access_token=create_access_token(user.id), user=_user_public(user))


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return _user_public(user)
