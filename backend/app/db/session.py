from collections.abc import Iterator
import logging
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
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(alembic_ini))
    # script_location이 'alembic' (상대경로)이라 cwd가 backend/가 아니면 깨짐.
    # 본 함수는 backend lifespan + scripts/CLI 양쪽에서 호출되므로 절대경로로 보강.
    cfg.set_main_option("script_location", str(alembic_ini.parent / "alembic"))
    startup_logger = logging.getLogger("autotrade.startup")
    scripts = ScriptDirectory.from_config(cfg)
    target_heads = tuple(scripts.get_heads())
    startup_logger.info(
        "[startup] migration graph loaded (revision_count=%d target_heads=%s)",
        sum(1 for _ in scripts.walk_revisions()),
        ",".join(target_heads) or "none",
    )
    with engine.connect() as connection:
        current_heads = tuple(MigrationContext.configure(connection).get_current_heads())
    startup_logger.info(
        "[startup] migration current heads before upgrade=%s",
        ",".join(current_heads) or "none",
    )
    backup_sqlite_before_upgrade(cfg)
    command.upgrade(cfg, "head")
    with engine.connect() as connection:
        applied_heads = tuple(MigrationContext.configure(connection).get_current_heads())
    startup_logger.info(
        "[startup] migration current heads after upgrade=%s",
        ",".join(applied_heads) or "none",
    )
    if set(applied_heads) != set(target_heads):
        raise RuntimeError("alembic upgrade completed without reaching configured head")


def backup_sqlite_before_upgrade(cfg, *, url: str | None = None) -> Path | None:
    """Create a consistent SQLite online backup before a pending migration.

    A failed backup aborts the migration. The database is never stamped or
    changed by this helper; in-memory/non-SQLite databases are left alone.
    """
    import sqlite3
    from datetime import datetime, timezone

    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import make_url

    rendered = url or engine.url.render_as_string(hide_password=False)
    parsed = make_url(rendered)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return None
    source = Path(parsed.database).expanduser().resolve()
    if not source.is_file():
        return None
    head = ScriptDirectory.from_config(cfg).get_current_head()
    src = sqlite3.connect(str(source))
    try:
        try:
            current = src.execute("select version_num from alembic_version").fetchone()
        except sqlite3.OperationalError:
            current = None
        if current is not None and current[0] == head:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = source.with_name(f"{source.name}.pre-{head}-{stamp}.bak")
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
        except Exception:
            dst.close()
            destination.unlink(missing_ok=True)
            raise
        else:
            dst.close()
    finally:
        src.close()
    return destination
