from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.config import Settings
from app.db import get_session
from app.models import User
from app.security import decode_token

SessionDep = Annotated[Session, Depends(get_session)]
_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def current_user(
    session: SessionDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    payload = decode_token(creds.credentials, settings.secret_key)
    if payload is None:
        raise unauthorized
    try:
        user = session.get(User, int(payload["sub"]))
    except (KeyError, ValueError):
        raise unauthorized from None
    if user is None or not user.active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for this role")
        return user

    return checker


OwnerOnly = Annotated[User, Depends(require_role("owner"))]


def can_manage_staff(user: User, staff_id: int) -> bool:
    return user.role == "owner" or (user.role == "staff" and user.staff_id == staff_id)
