from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:"):
        path = url.removeprefix("sqlite:///")
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


_settings = get_settings()
_ensure_sqlite_dir(_settings.database_url)

_connect_args: dict = (
    {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(_settings.database_url, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_migrations() -> None:
    """Run `alembic upgrade head` against the configured database.

    Schema evolution lives in alembic/versions/ — call this on startup so
    the production DB picks up new columns/tables without manual steps.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(alembic_ini))
    command.upgrade(cfg, "head")
