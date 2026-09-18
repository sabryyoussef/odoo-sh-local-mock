from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


@event.listens_for(engine, "connect")
def _sqlite_fk(dbapi_connection, connection_record) -> None:  # noqa: ARG001
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    from app.migrate import migrate_schema, migrate_subscription_data
    from app.services.catalog_service import seed_demo_catalog
    from app.services.subscription_service import seed_demo_subscription

    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)
    migrate_subscription_data(engine)
    with SessionLocal() as db:
        seed_demo_subscription(db)
        seed_demo_catalog(db)
        from app.services.module_catalog_service import seed_odoo_versions
        from app.services.platform_plan_service import seed_platform_plan_module_rules

        seed_odoo_versions(db)
        seed_platform_plan_module_rules(db)
        from app.services.cloud_catalog_service import seed_helpers_cloud

        seed_helpers_cloud(db)
        from app.services.helper_compute.store import seed_helper_compute

        seed_helper_compute(db)
