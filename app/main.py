from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel import Session, SQLModel, select

from app import __version__
from app.config import Settings
from app.db import make_engine
from app.models import User
from app.ratelimit import SlidingWindowLimiter
from app.routers import auth, bookings, catalog
from app.security import hash_password


def seed_owner(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if not (settings.owner_email and settings.owner_password):
        return
    email = settings.owner_email.lower()
    with Session(app.state.engine) as session:
        if session.exec(select(User).where(User.email == email)).first() is None:
            session.add(
                User(
                    email=email, password_hash=hash_password(settings.owner_password), role="owner"
                )
            )
            session.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_tables:
            SQLModel.metadata.create_all(app.state.engine)
        seed_owner(app)
        yield

    app = FastAPI(
        title="Appointment Booking API",
        version=__version__,
        description="Services, staff, weekly availability, time off and bookings "
        "that cannot double-book.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.login_limiter = SlidingWindowLimiter(
        settings.login_rate_limit, settings.login_rate_window_seconds
    )

    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(bookings.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        with Session(app.state.engine) as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
