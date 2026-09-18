"""CHECKPOINT E1.3R — real disposable adapter validation (opt-in integration tests).

Exercises the REAL default adapters against completely disposable
PostgreSQL databases and filesystem paths. No production, customer,
template, or approved reusable database is ever touched.

Naming pattern: chk_e1_3r_*

This test file requires live PostgreSQL at the endpoints configured in
app.config.Settings (build-postgres container). It is explicitly opt-in
so that normal unit test suites do not connect to PostgreSQL.

Run (inside control-api container):
    pytest tests/test_cloud_demo_clone_checkpoint_e1_3r.py -q --tb=short

Run (outside):
    docker exec odoo-sh-local-mock-control-api-1 python -m pytest tests/test_cloud_demo_clone_checkpoint_e1_3r.py -q --tb=short
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, event, select

from app.db import Base
from app.models import (
    CloudApplicationPackage,
    CloudInstance,
    CloudOdooVersion,
    CloudOrder,
    CloudPlan,
    CloudProvisioningRequest,
    CloudSubscription,
    CloudTemplate,
    Tenant,
    User,
)
from app.product_lines import (
    CLOUD_ADAPTER_DEMO_CLONE,
    CLOUD_DEMO_TEMPLATE_KIND,
    CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO,
    CLOUD_PROVISION_QUEUED,
    PRODUCT_LINE_HELPERS_CLOUD,
)
from app.services.postgres_service import _admin_connect, database_exists, re_fullmatch_safe
from app.services.tenant_postgres_service import (
    clone_database_from_template,
    create_tenant_role,
    drop_tenant_database,
    drop_tenant_role,
)
from app.services.cloud_demo_clone_service import (
    _DefaultDatabaseCloneAdapter,
    _DefaultDemoUserAdapter,
    _DefaultFilestoreCopyAdapter,
    DemoCloneIdentifiers,
    _validate_identifiers,
)


# ---------------------------------------------------------------------------
# Pytest option to skip entire module when --no-postgres is passed or
# when the --check-e1_3r marker is absent.
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "check_e1_3r: CHECKPOINT E1.3R real disposable adapter validation (requires live PostgreSQL)",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _pg_settings():
    """Load PG settings from the running config."""
    from app.config import get_settings
    return get_settings()


@pytest.fixture()
def disposable_prefix():
    """Unique prefix for this test invocation."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    rand = secrets.token_hex(2)
    return f"chk_e1_3r_{ts}_{rand}"


def _make_file_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(bind=engine)
    try:
        from app.migrate_dp6 import migrate_dp6_schema
        migrate_dp6_schema(engine)
    except Exception:
        pass
    return engine, path


def _cleanup_file(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except Exception:
            pass


def _seed_helpers_cloud(session):
    from app.services.cloud_catalog_service import seed_helpers_cloud
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()


def _prep_plan_version_package(db, *, plan_code="e13r_plan", plan_is_demo=True):
    from sqlalchemy.orm import sessionmaker
    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == plan_code))
    if plan is None:
        plan = CloudPlan(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code=plan_code,
            name=plan_code,
            is_demo=plan_is_demo,
            active=True,
        )
        db.add(plan)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if version is None:
        version = CloudOdooVersion(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="19.0",
            display_name="Odoo 19",
            edition="community",
            active=True,
        )
        db.add(version)
    package = db.scalar(
        select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading")
    )
    if package is None:
        package = CloudApplicationPackage(
            product_line=PRODUCT_LINE_HELPERS_CLOUD,
            code="trading",
            name="Trading",
            active=True,
        )
        db.add(package)
    db.flush()
    return plan, version, package


# ---------------------------------------------------------------------------
# Disposable PG helpers
# ---------------------------------------------------------------------------

def _create_disposable_source_db(prefix: str, *, filestore_dir: Path | None = None):
    """Create a tiny disposable source DB and optional filestore.

    Returns (src_db, src_role, src_fs).
    """
    src_db = f"{prefix}_src"
    src_role = f"{prefix}_r_src"

    assert re_fullmatch_safe(src_db), f"src_db unsafe: {src_db}"
    assert re_fullmatch_safe(src_role), f"src_role unsafe: {src_role}"
    assert not database_exists(src_db), f"src_db already exists: {src_db}"

    # create role
    create_tenant_role(src_role, secrets.token_urlsafe(16))

    # create DB
    conn = _admin_connect()
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        from psycopg2 import sql
        cur.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(src_db), sql.Identifier(src_role)
            )
        )
    conn.close()

    # seed minimal Odoo schema + synthetic data
    from app.config import get_settings
    settings = get_settings()
    src_conn = psycopg2.connect(
        host=settings.build_postgres_host,
        port=settings.build_postgres_port,
        user=settings.build_postgres_admin_user,
        password=settings.build_postgres_admin_password,
        dbname=src_db,
    )
    src_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with src_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ir_module_category (id SERIAL PRIMARY KEY, name VARCHAR);
            CREATE TABLE IF NOT EXISTS res_groups (id SERIAL PRIMARY KEY, name VARCHAR, category_id INT);
            CREATE TABLE IF NOT EXISTS res_partner (id SERIAL PRIMARY KEY, name VARCHAR, email VARCHAR, active BOOLEAN);
            CREATE TABLE IF NOT EXISTS res_users (id SERIAL PRIMARY KEY, login VARCHAR UNIQUE, password VARCHAR, partner_id INT, active BOOLEAN, company_id INT, create_date TIMESTAMP, share BOOLEAN);
            CREATE TABLE IF NOT EXISTS res_groups_users_rel (gid INT, uid INT, PRIMARY KEY (gid, uid));
            CREATE TABLE IF NOT EXISTS chk_validation (id SERIAL PRIMARY KEY, data TEXT, checksum TEXT);
        """)
        cur.execute("INSERT INTO ir_module_category (name) VALUES ('Internal User') ON CONFLICT DO NOTHING;")
        cur.execute("INSERT INTO ir_module_category (name) VALUES ('Administration') ON CONFLICT DO NOTHING;")
        cur.execute("INSERT INTO ir_module_category (name) VALUES ('Technical') ON CONFLICT DO NOTHING;")
        cur.execute("SELECT id FROM ir_module_category WHERE name='Internal User' LIMIT 1;")
        cat_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM ir_module_category WHERE name='Administration' LIMIT 1;")
        admin_cat = cur.fetchone()[0]
        cur.execute("SELECT id FROM ir_module_category WHERE name='Technical' LIMIT 1;")
        tech_cat = cur.fetchone()[0]
        cur.execute("INSERT INTO res_groups (name, category_id) VALUES ('Internal User', %s) ON CONFLICT DO NOTHING;", (cat_id,))
        cur.execute("INSERT INTO res_groups (name, category_id) VALUES ('Settings', %s) ON CONFLICT DO NOTHING;", (admin_cat,))
        cur.execute("INSERT INTO res_groups (name, category_id) VALUES ('Administration', %s) ON CONFLICT DO NOTHING;", (admin_cat,))
        cur.execute("INSERT INTO res_groups (name, category_id) VALUES ('Technical', %s) ON CONFLICT DO NOTHING;", (tech_cat,))
        # synthetic data
        synthetic = f"chk_e1_3r_synthetic_{prefix}"
        checksum = hashlib.sha256(synthetic.encode()).hexdigest()
        cur.execute("INSERT INTO chk_validation (data, checksum) VALUES (%s, %s);", (synthetic, checksum))
        cur.execute("INSERT INTO chk_validation (data, checksum) VALUES (%s, %s);", (f"second_{synthetic}", hashlib.sha256(f"second_{synthetic}".encode()).hexdigest()))
        # admin user for negative test
        cur.execute("INSERT INTO res_partner (name, email, active) VALUES ('Admin', 'admin@demo.local', TRUE) RETURNING id;")
        partner = cur.fetchone()[0]
        cur.execute("INSERT INTO res_users (login, password, partner_id, active, company_id, create_date, share) VALUES ('admin', 'admin_secret_123', %s, TRUE, 1, NOW(), FALSE);", (partner,))
    src_conn.close()

    # optional filestore
    src_fs = Path(f"/tmp/{prefix}_src_filestore")
    if filestore_dir is not None:
        src_fs = filestore_dir / "src_filestore"
    src_fs.mkdir(parents=True, exist_ok=False)
    (src_fs / "ordinary.txt").write_text(f"ordinary test {prefix}\n")
    (src_fs / "nested" / "deep").mkdir(parents=True, exist_ok=True)
    (src_fs / "nested" / "deep" / "nested.txt").write_text(f"nested test {prefix}\n")

    return src_db, src_role, src_fs


def _cleanup_disposable(prefix, *, src_db=None, dst_db=None, src_role=None, dst_role=None, dirs=None):
    """Best-effort cleanup of disposable artifacts."""
    for db in [dst_db, src_db]:
        if db and database_exists(db):
            try:
                drop_tenant_database(db)
            except Exception:
                pass
    for role in [dst_role, src_role]:
        if role:
            try:
                drop_tenant_role(role)
            except Exception:
                pass
    for d in (dirs or []):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test: real DB clone adapter creates unique destination with matching data
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_real_db_clone_adapter_creates_unique_destination_with_matching_data():
    """Real _DefaultDatabaseCloneAdapter clones disposable source to unique destination."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    src_db = f"{prefix}_src"
    dst_db = f"{prefix}_dst"
    src_role = f"{prefix}_r_src"
    dst_role = f"{prefix}_r_dst"
    try:
        src_db, src_role, src_fs = _create_disposable_source_db(prefix)
        dst_role_created = False
        create_tenant_role(dst_role, secrets.token_urlsafe(16))
        dst_role_created = True

        # record source rows
        from app.config import get_settings
        settings = get_settings()
        src_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=src_db,
        )
        src_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with src_conn.cursor() as cur:
            cur.execute("SELECT data, checksum FROM chk_validation ORDER BY id;")
            src_rows = cur.fetchall()
        src_conn.close()

        # clone via real adapter
        dba = _DefaultDatabaseCloneAdapter()
        dba.clone_database(src_db, dst_db, dst_role)
        assert database_exists(dst_db), "destination DB not created"

        # destination matches source
        dst_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=dst_db,
        )
        dst_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with dst_conn.cursor() as cur:
            cur.execute("SELECT data, checksum FROM chk_validation ORDER BY id;")
            dst_rows = cur.fetchall()
        dst_conn.close()
        assert dst_rows == src_rows, f"destination data mismatch: {dst_rows} != {src_rows}"

        # destination is independent: insert into dst does not affect src
        dst_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=dst_db,
        )
        dst_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with dst_conn.cursor() as cur:
            cur.execute("INSERT INTO chk_validation (data, checksum) VALUES ('dst_only', 'abc');")
        dst_conn.close()

        src_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=src_db,
        )
        src_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with src_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chk_validation WHERE data='dst_only';")
            assert cur.fetchone()[0] == 0, "dst_only leaked to source!"
        src_conn.close()
    finally:
        _cleanup_disposable(prefix, src_db=src_db, dst_db=dst_db, src_role=src_role, dst_role=dst_role, dirs=[src_fs] if 'src_fs' in dir() else [])


# ---------------------------------------------------------------------------
# Test: real filestore copy adapter copies and isolates
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_real_filestore_copy_adapter_copies_and_isolates():
    """Real _DefaultFilestoreCopyAdapter copies disposable filestore to isolated target."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    host_root = Path(f"/home/sabry/tmp/{prefix}")
    host_root.mkdir(parents=True, exist_ok=False)
    src_fs = host_root / "src_filestore"
    dst_fs = host_root / "dst_filestore"
    try:
        src_fs.mkdir(parents=True, exist_ok=False)
        (src_fs / "ordinary.txt").write_text(f"ordinary test {prefix}\n")
        (src_fs / "nested" / "deep").mkdir(parents=True, exist_ok=True)
        (src_fs / "nested" / "deep" / "nested.txt").write_text(f"nested test {prefix}\n")

        def file_checksum(p):
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()

        src_ordinary_cs = file_checksum(src_fs / "ordinary.txt")
        src_nested_cs = file_checksum(src_fs / "nested" / "deep" / "nested.txt")

        fsa = _DefaultFilestoreCopyAdapter()
        fsa.copy_filestore(src_fs, dst_fs)
        assert dst_fs.exists(), "destination filestore not created"
        assert (dst_fs / "ordinary.txt").exists(), "ordinary.txt missing"
        assert (dst_fs / "nested" / "deep" / "nested.txt").exists(), "nested.txt missing"
        assert file_checksum(dst_fs / "ordinary.txt") == src_ordinary_cs
        assert file_checksum(dst_fs / "nested" / "deep" / "nested.txt") == src_nested_cs

        # isolation: modify dst does not affect src
        (dst_fs / "ordinary.txt").write_text("modified\n")
        assert file_checksum(src_fs / "ordinary.txt") == src_ordinary_cs, "source modified!"
    finally:
        shutil.rmtree(host_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test: restricted demo user creation and restrictions
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_restricted_demo_user_creation_and_restrictions():
    """Real _DefaultDemoUserAdapter creates restricted user with correct attributes."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    src_db = f"{prefix}_src"
    dst_db = f"{prefix}_dst"
    src_role = f"{prefix}_r_src"
    dst_role = f"{prefix}_r_dst"
    try:
        src_db, src_role, src_fs = _create_disposable_source_db(prefix)
        dst_role_created = False
        create_tenant_role(dst_role, secrets.token_urlsafe(16))
        dst_role_created = True

        dba = _DefaultDatabaseCloneAdapter()
        dba.clone_database(src_db, dst_db, dst_role)

        ua = _DefaultDemoUserAdapter()
        demo_login = f"demo_chk_{prefix[-8:]}"
        demo_password = secrets.token_urlsafe(16)
        ua.create_restricted_user(
            db_name=dst_db,
            role_name=dst_role,
            role_password=secrets.token_urlsafe(16),
            login=demo_login,
            password=demo_password,
        )

        # verify user exists and is restricted
        from app.config import get_settings
        settings = get_settings()
        dst_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=dst_db,
        )
        dst_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with dst_conn.cursor() as cur:
            cur.execute("SELECT id, login, active, share FROM res_users WHERE login=%s;", (demo_login,))
            row = cur.fetchone()
            assert row, f"demo user {demo_login} not found"
            uid = row[0]
            assert row[2] is True, "demo user not active"
            assert row[3] is True, "demo user not shared"

            cur.execute("SELECT g.name FROM res_groups g JOIN res_groups_users_rel r ON r.gid=g.id WHERE r.uid=%s;", (uid,))
            groups = [r[0] for r in cur.fetchall()]
            assert "Internal User" in groups, "missing Internal User group"
            assert "Administration" not in groups, "demo user has Administration"
            assert "Settings" not in groups, "demo user has Settings"
            assert "Technical" not in groups, "demo user has Technical"

            # no template admin credentials
            cur.execute("SELECT password FROM res_users WHERE login='admin';")
            admin_pw = cur.fetchone()[0]
            cur.execute("SELECT password FROM res_users WHERE login=%s;", (demo_login,))
            demo_pw = cur.fetchone()[0]
            assert demo_pw != admin_pw, "demo password equals admin"
            assert demo_pw != "admin_secret_123", "demo password is admin secret"

        dst_conn.close()
    finally:
        _cleanup_disposable(prefix, src_db=src_db, dst_db=dst_db, src_role=src_role, dst_role=dst_role, dirs=[src_fs] if 'src_fs' in dir() else [])


# ---------------------------------------------------------------------------
# Test: source never modified
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_source_never_modified_by_clone_or_user():
    """Source DB and filestore are never deleted or modified."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    src_db = f"{prefix}_src"
    dst_db = f"{prefix}_dst"
    src_role = f"{prefix}_r_src"
    dst_role = f"{prefix}_r_dst"
    try:
        src_db, src_role, src_fs = _create_disposable_source_db(prefix)
        dst_role_created = False
        create_tenant_role(dst_role, secrets.token_urlsafe(16))
        dst_role_created = True

        def file_checksum(p):
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()

        src_ordinary_cs = file_checksum(src_fs / "ordinary.txt")

        dba = _DefaultDatabaseCloneAdapter()
        dba.clone_database(src_db, dst_db, dst_role)

        # insert into dst + create restricted user in dst
        from app.config import get_settings
        settings = get_settings()
        dst_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=dst_db,
        )
        dst_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with dst_conn.cursor() as cur:
            cur.execute("INSERT INTO chk_validation (data, checksum) VALUES ('dst_only', 'abc');")
        dst_conn.close()

        ua = _DefaultDemoUserAdapter()
        ua.create_restricted_user(
            db_name=dst_db, role_name=dst_role, role_password=secrets.token_urlsafe(16),
            login=f"demo_{prefix[-8:]}", password=secrets.token_urlsafe(16),
        )

        # source unchanged
        src_conn = psycopg2.connect(
            host=settings.build_postgres_host, port=settings.build_postgres_port,
            user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password,
            dbname=src_db,
        )
        src_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with src_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chk_validation;")
            cnt = cur.fetchone()[0]
            assert cnt == 2, f"source modified: expected 2 rows, got {cnt}"
            cur.execute("SELECT count(*) FROM chk_validation WHERE data='dst_only';")
            assert cur.fetchone()[0] == 0, "dst_only leaked to source"
        src_conn.close()

        assert file_checksum(src_fs / "ordinary.txt") == src_ordinary_cs, "source filestore modified"
    finally:
        _cleanup_disposable(prefix, src_db=src_db, dst_db=dst_db, src_role=src_role, dst_role=dst_role, dirs=[src_fs] if 'src_fs' in dir() else [])


# ---------------------------------------------------------------------------
# Test: pre-existing destination preserved
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_pre_existing_destination_preserved():
    """Pre-existing destination DB is not overwritten by clone."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    src_db = f"{prefix}_src"
    dst_db = f"{prefix}_dst"
    src_role = f"{prefix}_r_src"
    dst_role = f"{prefix}_r_dst"
    try:
        src_db, src_role, src_fs = _create_disposable_source_db(prefix)
        dst_role_created = False
        create_tenant_role(dst_role, secrets.token_urlsafe(16))
        dst_role_created = True

        # create pre-existing dst
        conn = _admin_connect()
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            from psycopg2 import sql
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(dst_db), sql.Identifier(src_role)
            ))
        conn.close()

        # clone should be idempotent (target already exists, no overwrite)
        dba = _DefaultDatabaseCloneAdapter()
        dba.clone_database(src_db, dst_db, dst_role)
        assert database_exists(dst_db), "pre-existing dst deleted"
        assert database_exists(src_db), "source deleted"
    finally:
        _cleanup_disposable(prefix, src_db=src_db, dst_db=dst_db, src_role=src_role, dst_role=dst_role, dirs=[src_fs] if 'src_fs' in dir() else [])


# ---------------------------------------------------------------------------
# Test: invalid identifier rejected
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_invalid_identifier_rejected():
    """Invalid database/role identifiers are rejected by re_fullmatch_safe."""
    assert not re_fullmatch_safe("123invalid")
    assert not re_fullmatch_safe("bad-name")
    assert not re_fullmatch_safe("bad/name")
    assert not re_fullmatch_safe("../traversal")
    assert not re_fullmatch_safe("a" * 64)
    assert not re_fullmatch_safe("")


# ---------------------------------------------------------------------------
# Test: traversal path rejected
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_traversal_path_rejected():
    """Traversal filestore paths are rejected by _validate_identifiers."""
    for tp in ["/tmp/../etc/passwd", "/tmp/chk/../../etc", "/", "/home/sabry"]:
        fake = DemoCloneIdentifiers(
            tenant_code="demo_clone_1_abcd",
            db_name="mosh_demo_1_abcd",
            role_name="mosh_demo_r_1_abcd",
            filestore_path=tp,
            demo_login="demo_test",
            run_id="test",
            request_id=1,
            rand="abcd",
        )
        with pytest.raises(ValueError):
            _validate_identifiers(fake, "template_db")


# ---------------------------------------------------------------------------
# Test: symlink parent escape rejected
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_symlink_parent_escape_rejected():
    """Symlink in destination parent is rejected."""
    tmpdir = Path(tempfile.mkdtemp(prefix="chk_e1_3r_symlink_"))
    try:
        link_path = tmpdir / "link_to_etc"
        link_path.symlink_to("/etc")
        fake = DemoCloneIdentifiers(
            tenant_code="demo_clone_1_abcd",
            db_name="mosh_demo_1_abcd",
            role_name="mosh_demo_r_1_abcd",
            filestore_path=str(link_path / "filestore"),
            demo_login="demo_test",
            run_id="test",
            request_id=1,
            rand="abcd",
        )
        # symlink parent exists and is symlink -> rejected
        with pytest.raises(ValueError, match="symlink"):
            _validate_identifiers(fake, "template_db")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test: restricted user cannot create/drop databases
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_restricted_user_cannot_create_drop_databases():
    """Restricted PG role cannot create or drop databases."""
    prefix = f"chk_e1_3r_{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()}_{secrets.token_hex(2)}"
    src_db = f"{prefix}_src"
    dst_db = f"{prefix}_dst"
    src_role = f"{prefix}_r_src"
    dst_role = f"{prefix}_r_dst"
    try:
        src_db, src_role, src_fs = _create_disposable_source_db(prefix)
        create_tenant_role(dst_role, secrets.token_urlsafe(16))
        dba = _DefaultDatabaseCloneAdapter()
        dba.clone_database(src_db, dst_db, dst_role)

        # get a known password for the role
        test_pw = secrets.token_urlsafe(16)
        conn = _admin_connect()
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            from psycopg2 import sql
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(dst_role)), (test_pw,))
        conn.close()

        # connect as restricted role
        from app.config import get_settings
        settings = get_settings()
        try:
            test_conn = psycopg2.connect(
                host=settings.build_postgres_host, port=settings.build_postgres_port,
                user=dst_role, password=test_pw, dbname="postgres",
            )
            test_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with test_conn.cursor() as cur:
                with pytest.raises(Exception):
                    cur.execute("CREATE DATABASE chk_e1_3r_should_fail;")
                with pytest.raises(Exception):
                    cur.execute("DROP DATABASE postgres;")
            test_conn.close()
        except Exception:
            # connection itself may fail (expected if role has no connect privilege)
            pass

        # cannot access unrelated DB
        try:
            src_test = psycopg2.connect(
                host=settings.build_postgres_host, port=settings.build_postgres_port,
                user=dst_role, password=test_pw, dbname=src_db,
            )
            src_test.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with src_test.cursor() as cur:
                cur.execute("SELECT count(*) FROM chk_validation;")
                cnt = cur.fetchone()[0]
                # if this succeeds, it's a leak
                pytest.fail(f"restricted role accessed unrelated DB, count={cnt}")
            src_test.close()
        except Exception:
            # expected: role has no access to src_db
            pass
    finally:
        _cleanup_disposable(prefix, src_db=src_db, dst_db=dst_db, src_role=src_role, dst_role=dst_role, dirs=[src_fs] if 'src_fs' in dir() else [])


# ---------------------------------------------------------------------------
# Test: result contains no credentials
# ---------------------------------------------------------------------------

@pytest.mark.check_e1_3r
def test_result_contains_no_credentials():
    """DemoCloneResult string does not contain passwords."""
    from app.services.cloud_demo_clone_service import DemoCloneResult
    result = DemoCloneResult(
        success=True, request_id=999, tenant_code="test",
        db_name="db", role_name="role", filestore_path="/tmp/fs",
        demo_login="demo_test",
    )
    result_str = str(result)
    assert "password" not in result_str.lower()
    assert "admin_secret_123" not in result_str
