from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import select

from app.deps import OwnerOnly, SessionDep, SettingsDep
from app.models import Staff, User
from app.schemas import LoginIn, TokenOut, UserCreate, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash of a random string, used so unknown emails cost the same time as wrong passwords.
_DUMMY_HASH = hash_password("not-a-real-password")


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, session: SessionDep, settings: SettingsDep) -> TokenOut:
    ip = request.client.host if request.client else "unknown"
    wait = request.app.state.login_limiter.check(f"ip:{ip}", f"email:{body.email.lower()}")
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    user = session.exec(select(User).where(User.email == body.email.lower())).first()
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    assert user.id is not None
    token = create_token(user.id, user.role, settings.secret_key, settings.token_minutes)
    return TokenOut(access_token=token, role=user.role)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, session: SessionDep, _: OwnerOnly) -> User:
    if body.role == "staff":
        if body.staff_id is None or session.get(Staff, body.staff_id) is None:
            raise HTTPException(422, "Staff users need a valid staff_id")
    elif body.staff_id is not None:
        raise HTTPException(422, "Owners do not take a staff_id")
    email = body.email.lower()
    if session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
        staff_id=body.staff_id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
