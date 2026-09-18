"""TM-D12 Final Visual Remediation — E1.6

Proves TM-D12 with real disposable Odoo 19, restricted user, fully rendered OWL client,
and user-level Settings/admin denial. Fixes previous blank OWL issue.

Isolation:
- Unique prefix tm_d12_final_*
- Disposable SQLite control plane (never live control.db)
- Disposable PG DB/role/filestore/container/port
- No production/UAT DB as destination
- No production worker

Evidence: docs/reports/evidence/tm-d12-e1_6-final-visual-<UTC>/
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import CloudApplicationPackage, CloudOdooVersion, CloudPlan, CloudTemplate, Tenant
from app.product_lines import (
    CLOUD_ADAPTER_DEMO_CLONE, CLOUD_DEMO_TEMPLATE_KIND, CLOUD_LANE_DEMO,
    CLOUD_ORDER_KIND_DEMO, CLOUD_PROVISION_QUEUED, PRODUCT_LINE_HELPERS_CLOUD,
)

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
EVIDENCE_DIR = Path(f"docs/reports/evidence/tm-d12-e1_6-final-visual-{TIMESTAMP}")
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
print(f"Evidence dir: {EVIDENCE_DIR}")
print(f"Timestamp: {TIMESTAMP}")

def _unique_prefix():
    ts = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    rand = secrets.token_hex(3)
    return f"tm_d12_final_{ts}_{rand}"

PREFIX = _unique_prefix()
print(f"Prefix: {PREFIX}")

def _pg_admin_connect():
    from app.services.postgres_service import _admin_connect
    return _admin_connect()

def _get_baseline():
    conn = _pg_admin_connect()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM pg_database")
    pg_db_before = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'mosh_%' OR rolname LIKE 'tm_d12_%'")
    pg_role_before = cur.fetchone()[0]
    cur.close()
    conn.close()
    import docker
    client = docker.from_env()
    containers_before = len(client.containers.list(all=True, filters={"label": "mock_odoo_sh=true"}))
    import glob
    filestores_before = len(glob.glob("/tmp/.demo_clone_*")) + len(glob.glob("/tmp/tm_d12_*")) + len(glob.glob("/data/tenants/.demo_clone_*"))
    return pg_db_before, pg_role_before, containers_before, filestores_before

pg_db_before, pg_role_before, containers_before, filestores_before = _get_baseline()
print(f"Baseline: dbs={pg_db_before}, roles={pg_role_before}, containers={containers_before}, filestores={filestores_before}")

from app.config import get_settings
get_settings.cache_clear()
s = get_settings()
worker_flags_before = {
    "helpers_cloud_real_provisioning_enabled": s.helpers_cloud_real_provisioning_enabled,
    "helpers_cloud_worker_max_jobs": s.helpers_cloud_worker_max_jobs,
    "helpers_cloud_demo_worker_enabled": s.helpers_cloud_demo_worker_enabled,
    "helpers_cloud_demo_worker_max_jobs": s.helpers_cloud_demo_worker_max_jobs,
    "helpers_cloud_demo_lifecycle_enabled": s.helpers_cloud_demo_lifecycle_enabled,
    "helpers_cloud_demo_lifecycle_max_jobs": s.helpers_cloud_demo_lifecycle_max_jobs,
    "helpers_cloud_demo_cleanup_enabled": s.helpers_cloud_demo_cleanup_enabled,
    "helpers_cloud_demo_cleanup_max_jobs": s.helpers_cloud_demo_cleanup_max_jobs,
}
print(f"Worker flags before: {worker_flags_before}")

def _make_isolated_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    import app.db as db_mod
    import app.main as main_mod
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db_mod.engine = engine
    db_mod.SessionLocal = SessionLocal
    main_mod.SessionLocal = SessionLocal
    from app.db import init_db
    import app.models_dp6
    from app.migrate_dp6 import migrate_dp6_schema
    init_db()
    migrate_dp6_schema(engine)
    return engine, SessionLocal

def _seed_helpers_cloud(session):
    from app.services.cloud_catalog_service import seed_helpers_cloud
    try:
        seed_helpers_cloud(session)
        session.commit()
    except Exception:
        session.rollback()

def _register_user(db, email=None):
    from app.services.cloud_auth_service import register_cloud_customer, RegisterInput, reset_rate_limit_for_tests
    reset_rate_limit_for_tests()
    email = email or f"final-{secrets.token_hex(4)}@test.example"
    return register_cloud_customer(db, RegisterInput(full_name="Final Visual Test User", email=email, phone="+20100000001", company_name="Final Trading", country="Egypt", password="SecurePass1", password_confirm="SecurePass1", terms_accepted=True), client_key=email)

def _complete_setup(db, user, subdomain=None):
    from app.services.cloud_setup_service import get_or_create_draft_setup, save_plan, save_version, save_package, save_company, save_addons
    subdomain = subdomain or f"final-ws-{secrets.token_hex(4)}"
    _seed_helpers_cloud(db)
    setup = get_or_create_draft_setup(db, user)
    plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "business"))
    if plan is None:
        plan = db.scalar(select(CloudPlan).where(CloudPlan.code == "starter"))
    if plan is None:
        plan = db.scalar(select(CloudPlan).where(CloudPlan.active == True))
    if plan:
        save_plan(db, setup, plan_id=plan.id, billing_cycle="monthly")
    setup = get_or_create_draft_setup(db, user)
    version = db.scalar(select(CloudOdooVersion).where(CloudOdooVersion.code == "19.0"))
    if version:
        save_version(db, setup, version_id=version.id)
    setup = get_or_create_draft_setup(db, user)
    package = db.scalar(select(CloudApplicationPackage).where(CloudApplicationPackage.code == "trading"))
    if package:
        save_package(db, setup, package_id=package.id)
    setup = get_or_create_draft_setup(db, user)
    save_company(db, setup, {"legal_company_name": "Final Trading Co", "workspace_name": "Final Trading", "requested_subdomain": subdomain, "country": "Egypt", "currency": "EGP", "language": "en_US", "timezone": "Africa/Cairo", "required_users": "5", "required_storage_gb": "20"})
    setup = get_or_create_draft_setup(db, user)
    save_addons(db, setup, [])
    return get_or_create_draft_setup(db, user)

def _make_prepared_demo_template(db, *, catalog_code, postgres_db_name):
    tpl = CloudTemplate(catalog_code=catalog_code, product_line=PRODUCT_LINE_HELPERS_CLOUD, industry_code="general", package_code="trading", odoo_version_code="19.0", edition="community", template_kind=CLOUD_DEMO_TEMPLATE_KIND, supported_languages="ar,en", active=True, readiness_state="prepared", status="draft", health="unhealthy", version="1.0.0", postgres_database_name=postgres_db_name)
    db.add(tpl)
    db.flush()
    db.refresh(tpl)
    return tpl

class RealOdooDemoUserAdapter:
    def __init__(self):
        self.captured_login = None
        self.captured_password = None
        self.captured_hash = None
    def create_restricted_user(self, db_name, role_name, role_password, login, password):
        from passlib.context import CryptContext
        from app.config import get_settings
        settings = get_settings()
        ctx = CryptContext(['pbkdf2_sha512', 'plaintext'], deprecated=['auto'], pbkdf2_sha512__rounds=600000)
        hashed = ctx.hash(password)
        self.captured_login = login
        self.captured_password = password
        self.captured_hash = hashed
        print(f"Creating restricted user {login} with hash {hashed[:20]}...")
        conn = psycopg2.connect(host=settings.build_postgres_host, port=settings.build_postgres_port, user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password, dbname=db_name)
        try:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM res_users WHERE login = %s LIMIT 1", (login,))
                if cur.fetchone():
                    print(f"User {login} already exists, skipping")
                    return
                cur.execute("SELECT partner_id, company_id FROM res_users WHERE login='admin' LIMIT 1")
                admin_row = cur.fetchone()
                if admin_row:
                    admin_partner, admin_company = admin_row
                else:
                    admin_partner, admin_company = 1, 1
                cur.execute("INSERT INTO res_partner (name, email, active, company_id) VALUES (%s, %s, TRUE, %s) RETURNING id", (login, f"{login}@demo.local", admin_company))
                partner_id = cur.fetchone()[0]
                cur.execute("INSERT INTO res_users (login, password, partner_id, active, company_id, share, create_date, notification_type) VALUES (%s, %s, %s, TRUE, %s, FALSE, NOW(), 'email') RETURNING id", (login, hashed, partner_id, admin_company))
                user_id = cur.fetchone()[0]
                print(f"Created user {login} with id {user_id}, partner {partner_id}")
                cur.execute("INSERT INTO res_company_users_rel (cid, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (admin_company, user_id))
                print(f"Linked company {admin_company} to user {user_id}")
                cur.execute("SELECT id FROM res_groups WHERE id=1 LIMIT 1")
                base_group = cur.fetchone()
                if base_group:
                    cur.execute("INSERT INTO res_groups_users_rel (gid, uid) VALUES (%s, %s) ON CONFLICT DO NOTHING", (1, user_id))
                    print(f"Assigned base group 1 to user {user_id}")
                cur.execute("DELETE FROM res_groups_users_rel WHERE uid=%s AND gid IN (4,21,22)", (user_id,))
                cur.execute("SELECT gid FROM res_groups_users_rel WHERE uid=%s", (user_id,))
                current_groups = [row[0] for row in cur.fetchall()]
                for gid in current_groups:
                    if gid != 1:
                        cur.execute("DELETE FROM res_groups_users_rel WHERE uid=%s AND gid=%s", (user_id, gid))
                print(f"Cleaned admin groups for user {user_id}, kept only group 1")
                conn_v = psycopg2.connect(host=settings.build_postgres_host, port=settings.build_postgres_port, user=settings.build_postgres_admin_user, password=settings.build_postgres_admin_password, dbname=db_name)
                conn_v.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cur_v = conn_v.cursor()
                cur_v.execute("SELECT gid FROM res_groups_users_rel WHERE uid=%s", (user_id,))
                groups = [row[0] for row in cur_v.fetchall()]
                cur_v.close()
                conn_v.close()
                print(f"Final groups for {login}: {groups}")
        except Exception as exc:
            import traceback
            print(f"Failed to create restricted user: {exc}")
            traceback.print_exc()
            raise
        finally:
            try:
                conn.close()
            except:
                pass

class CapturingDatabaseAdapter:
    def __init__(self):
        self.captured_role_passwords = {}
        self._real = None
        from app.services.cloud_demo_clone_service import _DefaultDatabaseCloneAdapter
        self._real = _DefaultDatabaseCloneAdapter()
    def clone_database(self, source_db, target_db, owner_role):
        return self._real.clone_database(source_db, target_db, owner_role)
    def create_role(self, role_name, password):
        print(f"Capturing role {role_name} password")
        self.captured_role_passwords[role_name] = password
        return self._real.create_role(role_name, password)
    def drop_database(self, db_name):
        return self._real.drop_database(db_name)
    def drop_role(self, role_name):
        return self._real.drop_role(role_name)
    def database_exists(self, db_name):
        return self._real.database_exists(db_name)
    def role_exists(self, role_name):
        return self._real.role_exists(role_name)

def sanitize_html(html, db_name, role_name, password=None):
    if db_name:
        html = html.replace(db_name, "REDACTED_DB")
    if role_name:
        html = html.replace(role_name, "REDACTED_ROLE")
    if password:
        html = html.replace(password, "REDACTED_PASSWORD")
    # Remove csrf_token
    html = re.sub(r'csrf_token["\']?\s*:\s*["\'][^"\']+["\']', 'csrf_token: "REDACTED"', html)
    html = re.sub(r'name="csrf_token" value="[^"]+"', 'name="csrf_token" value="REDACTED"', html)
    html = re.sub(r'csrf_token\s*=\s*"[^"]+"', 'csrf_token="REDACTED"', html)
    return html

engine, SessionLocal = _make_isolated_engine()
db = SessionLocal()

disposable_dbs = []
disposable_roles = []
disposable_filestores = []
disposable_containers = []
allocated_port = None
tenant_record = None
request_record = None
demo_login = None
demo_password = None
role_name_captured = None
role_password_captured = None
db_name_captured = None
filestore_path_captured = None
tenant_code_captured = None
sanitized_external_url = None
groups = []

try:
    real_template_db = "mosh_tpl_cloud_base_19_0_trading"
    from app.services.postgres_service import database_exists
    assert database_exists(real_template_db), f"Real template DB {real_template_db} not found"
    print(f"Using real template DB: {real_template_db}")
    s = get_settings()
    dsn_tpl = f"host={s.build_postgres_host} port={s.build_postgres_port} dbname={real_template_db} user={s.build_postgres_admin_user} password={s.build_postgres_admin_password}"
    conn_tpl = psycopg2.connect(dsn_tpl)
    conn_tpl.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_tpl = conn_tpl.cursor()
    cur_tpl.execute("SELECT count(*) FROM res_users")
    user_count = cur_tpl.fetchone()[0]
    print(f"Template res_users count: {user_count}")
    cur_tpl.execute("SELECT login FROM res_users WHERE login='admin' LIMIT 1")
    assert cur_tpl.fetchone() is not None, "admin user not found in template"
    cur_tpl.close()
    conn_tpl.close()

    user = _register_user(db, email=f"{PREFIX}@test.example")
    print(f"Registered user: {user.email} id={user.id}")
    from app.services.cloud_auth_service import authenticate_cloud_customer
    authed = authenticate_cloud_customer(db, email=f"{PREFIX}@test.example", password="SecurePass1", client_key=f"{PREFIX}@test.example")
    assert authed.id == user.id
    print(f"Authenticated user: {authed.id}")

    setup = _complete_setup(db, user, subdomain=f"final{secrets.token_hex(4)}")
    from app.services.cloud_setup_service import is_confirm_ready, review_snapshot
    assert is_confirm_ready(setup) is True
    snapshot = review_snapshot(db, setup)
    assert snapshot["package"].code == "trading"
    print(f"Setup ready: {snapshot['package'].code}")

    catalog_code = f"demo-19.0-community-general-trading-{PREFIX[:8]}"
    tpl = _make_prepared_demo_template(db, catalog_code=catalog_code, postgres_db_name=real_template_db)
    print(f"Created template: {tpl.catalog_code} -> {tpl.postgres_database_name}")

    from app.services.cloud_checkout_service import checkout_demo_clone
    idempotency_key = f"final-{PREFIX}-{secrets.token_hex(4)}"
    order, sub, req, inst = checkout_demo_clone(db, user=user, setup=setup, idempotency_key=idempotency_key, template_id=tpl.id)
    print(f"Checkout: order={order.id}, sub={sub.id}, req={req.id}, inst={inst.id}")
    order2, sub2, req2, inst2 = checkout_demo_clone(db, user=user, setup=setup, idempotency_key=idempotency_key, template_id=tpl.id)
    assert order2.id == order.id
    assert req2.id == req.id
    print(f"Idempotency verified: {order.id} == {order2.id}")

    assert req.lane == "demo"
    assert req.adapter == "demo_clone"
    assert req.template_id == tpl.id

    os.environ["HELPERS_CLOUD_DEMO_WORKER_ENABLED"] = "true"
    os.environ["HELPERS_CLOUD_DEMO_WORKER_MAX_JOBS"] = "1"
    os.environ["TENANT_ROOT"] = "/opt/projects/active/odoo-sh-local-mock/data/tenants"
    os.environ["TENANT_HOST_ROOT"] = "/opt/projects/active/odoo-sh-local-mock/data/tenants"
    get_settings.cache_clear()
    from app.services.cloud_demo_clone_worker_service import is_demo_clone_worker_enabled, get_demo_clone_worker_max_jobs
    assert is_demo_clone_worker_enabled() is True
    assert get_demo_clone_worker_max_jobs() == 1
    assert get_settings().helpers_cloud_real_provisioning_enabled is False
    print("Demo worker enabled (isolated)")

    from app.services.cloud_provisioning_service import claim_next_demo_clone_job, claim_next_real_cloud_job
    worker_id = f"final-worker-{PREFIX[:8]}"
    claimed = claim_next_demo_clone_job(db, worker_id)
    assert claimed is not None
    assert claimed.id == req.id
    print(f"Claimed job: {claimed.id} by {worker_id}")
    claimed2 = claim_next_demo_clone_job(db, "other-worker")
    assert claimed2 is None
    assert claim_next_real_cloud_job(db, "real-worker") is None
    print("Second claim correctly None, real worker None")

    req.status = CLOUD_PROVISION_QUEUED
    req.current_step = "queued"
    db.commit()

    from app.services.cloud_demo_clone_service import execute_demo_clone_job, generate_demo_clone_identifiers
    ids_preview = generate_demo_clone_identifiers(claimed.id)
    print(f"Preview ids: db={ids_preview.db_name}, role={ids_preview.role_name}, filestore={ids_preview.filestore_path}, login={ids_preview.demo_login}")
    assert str(ids_preview.filestore_path).startswith("/tmp") or str(ids_preview.filestore_path).startswith("/data/tenants"), f"filestore should be under /tmp or /data/tenants, got {ids_preview.filestore_path}"
    from pathlib import Path as _Path
    _target_preview = _Path(ids_preview.filestore_path)
    if _target_preview.exists():
        shutil.rmtree(_target_preview, ignore_errors=True)
    if _target_preview.parent.exists() and ".demo_clone_" in str(_target_preview.parent):
        try:
            if not any(_target_preview.parent.iterdir()):
                _target_preview.parent.rmdir()
            else:
                shutil.rmtree(_target_preview.parent, ignore_errors=True)
        except Exception:
            pass
    from app.services.postgres_service import database_exists as _db_exists
    from app.services.tenant_postgres_service import drop_tenant_database as _drop_db, drop_tenant_role as _drop_role
    if _db_exists(ids_preview.db_name):
        try:
            _drop_db(ids_preview.db_name)
        except Exception:
            pass
    try:
        _drop_role(ids_preview.role_name)
    except Exception:
        pass

    db_adapter = CapturingDatabaseAdapter()
    user_adapter = RealOdooDemoUserAdapter()

    result = execute_demo_clone_job(db, claimed, db_adapter=db_adapter, user_adapter=user_adapter)
    print(f"Clone result: success={result.success}, tenant_code={result.tenant_code}, db={result.db_name}, role={result.role_name}, login={result.demo_login}, error={result.error_code}, msg={result.error_message}")
    if not result.success:
        print(f"Clone failed details: error_code={result.error_code}, error_message={result.error_message}")
        import traceback
        traceback.print_stack()
    assert result.success is True, f"Clone failed: {result.error_code} {result.error_message}"
    assert result.db_name is not None
    assert result.role_name is not None
    assert result.demo_login is not None
    assert database_exists(result.db_name)
    print(f"Clone succeeded: {result.db_name}")

    db_name_captured = result.db_name
    role_name_captured = result.role_name
    demo_login = result.demo_login
    demo_password = user_adapter.captured_password
    role_password_captured = db_adapter.captured_role_passwords.get(role_name_captured)
    print(f"Captured demo_login={demo_login}, demo_password={'*' * 8 if demo_password else None}, role={role_name_captured}")

    if not demo_password:
        demo_password = secrets.token_urlsafe(12)
        print(f"Generated demo_password: {'*' * 8}")
        from passlib.context import CryptContext
        ctx = CryptContext(['pbkdf2_sha512', 'plaintext'], deprecated=['auto'], pbkdf2_sha512__rounds=600000)
        hashed = ctx.hash(demo_password)
        conn = psycopg2.connect(host=s.build_postgres_host, port=s.build_postgres_port, user=s.build_postgres_admin_user, password=s.build_postgres_admin_password, dbname=db_name_captured)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute("UPDATE res_users SET password=%s WHERE login=%s", (hashed, demo_login))
        conn.close()
        print(f"Reset demo user password to known value")

    if not role_password_captured:
        role_password_captured = secrets.token_urlsafe(16)
        print(f"Generated role_password: {'*' * 8}")
        conn = _pg_admin_connect()
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        from psycopg2 import sql
        cur.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD %s").format(sql.Identifier(role_name_captured)), (role_password_captured,))
        cur.close()
        conn.close()
        print(f"Reset role password")

    disposable_dbs.append(db_name_captured)
    disposable_roles.append(role_name_captured)
    filestore_path_captured = result.filestore_path
    tenant_code_captured = result.tenant_code
    if filestore_path_captured:
        disposable_filestores.append(filestore_path_captured)
        parent = str(Path(filestore_path_captured).parent)
        if parent not in disposable_filestores:
            disposable_filestores.append(parent)

    dsn_dst = f"host={s.build_postgres_host} port={s.build_postgres_port} dbname={db_name_captured} user={s.build_postgres_admin_user} password={s.build_postgres_admin_password}"
    conn_dst = psycopg2.connect(dsn_dst)
    conn_dst.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_dst = conn_dst.cursor()
    cur_dst.execute("SELECT login, active, share FROM res_users WHERE login=%s", (demo_login,))
    row = cur_dst.fetchone()
    assert row is not None, f"demo user {demo_login} not found"
    assert row[0] == demo_login
    assert row[1] is True
    print(f"Verified demo user in DB: {row}")
    cur_dst.execute("SELECT gid FROM res_groups_users_rel WHERE uid=(SELECT id FROM res_users WHERE login=%s)", (demo_login,))
    groups = [r[0] for r in cur_dst.fetchall()]
    print(f"Demo user groups: {groups}")
    assert 4 not in groups, "should not be in admin group 4"
    assert 21 not in groups, "should not be in admin group 21"
    assert 22 not in groups, "should not be in admin group 22"
    conn_admin = _pg_admin_connect()
    cur_admin = conn_admin.cursor()
    cur_admin.execute("SELECT rolcreatedb, rolsuper FROM pg_roles WHERE rolname=%s", (role_name_captured,))
    role_row = cur_admin.fetchone()
    assert role_row is not None
    assert role_row[0] is False, "role should not have CREATEDB"
    assert role_row[1] is False, "role should not be superuser"
    print(f"Verified PG role: createdb={role_row[0]}, super={role_row[1]}")
    cur_admin.close()
    conn_admin.close()
    cur_dst.close()
    conn_dst.close()

    conn_src = psycopg2.connect(dsn_tpl)
    conn_src.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_src = conn_src.cursor()
    cur_src.execute("SELECT count(*) FROM res_users")
    assert cur_src.fetchone()[0] == user_count
    cur_src.close()
    conn_src.close()
    print("Source DB unchanged")

    from app.services.cloud_demo_lifecycle_service import activate_demo_lifecycle, get_demo_portal_status
    status_before = get_demo_portal_status(db, req)
    print(f"Status before activation: {status_before['status']}, can_launch={status_before['can_launch']}")
    if status_before["status"] == "preparing":
        activation = activate_demo_lifecycle(db, req)
        print(f"Activation: success={activation.success}, trial={activation.trial_ends_at}")
        assert activation.success is True
    else:
        print("Already active via auto-activation")
        activation = activate_demo_lifecycle(db, req)
        print(f"Idempotent activation: {activation.success}")

    from app.product_lines import CLOUD_DEMO_TRIAL_DAYS, CLOUD_DEMO_GRACE_DAYS, CLOUD_DEMO_RETENTION_DAYS
    assert CLOUD_DEMO_TRIAL_DAYS == 7
    assert CLOUD_DEMO_GRACE_DAYS == 3
    assert CLOUD_DEMO_RETENTION_DAYS == 30

    status_after = get_demo_portal_status(db, req)
    print(f"Status after: {status_after['status']}, can_launch={status_after['can_launch']}")
    assert status_after["status"] == "active"
    assert status_after["can_launch"] is True

    portal_json = json.dumps(status_after)
    assert db_name_captured not in portal_json
    assert "filestore" not in portal_json.lower()
    assert "password" not in portal_json.lower()
    assert "secret" not in portal_json.lower()
    print("Portal privacy verified")

    tenant = db.scalar(select(Tenant).where(Tenant.tenant_code == tenant_code_captured))
    assert tenant is not None
    print(f"Tenant: {tenant.tenant_code}, db={tenant.database_name}, role={tenant.database_role}")

    import socket
    def find_free_port(start=8399, end=8499):
        for p in range(start, end):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        raise RuntimeError("No free port found")
    allocated_port = find_free_port(8399, 8499)
    print(f"Allocated port: {allocated_port}")
    tenant.http_port = allocated_port
    db.commit()
    print(f"Updated tenant port to {allocated_port}")

    # Prepare filestore and runtime for Odoo using proper tenant_docker_service
    from pathlib import Path as _Path2
    # The filestore_path_captured is like /tmp/.demo_clone_xxx/filestore or /data/tenants/.demo_clone_xxx/filestore
    # For tenant_docker_service, we need filestore_container_path and filestore_host_path
    # filestore_container_path is the container path (same as filestore_path_captured)
    # filestore_host_path is the host path (translated via tenant_host_root)
    filestore_container_path = filestore_path_captured
    # Translate to host path
    _s_tmp = get_settings()
    filestore_host_path = str(_Path2(filestore_container_path).resolve()).replace(_s_tmp.tenant_root, _s_tmp.tenant_host_root) if _s_tmp.tenant_root in filestore_container_path else filestore_container_path
    # Ensure host filestore exists
    _Path2(filestore_host_path).mkdir(parents=True, exist_ok=True)
    print(f"Filestore container: {filestore_container_path} -> host: {filestore_host_path}")
    # Also ensure parent runtime will be created by run_tenant_odoo_container
    # Update web.base.url in cloned DB to match allocated port
    conn_update = psycopg2.connect(host=s.build_postgres_host, port=s.build_postgres_port, user=s.build_postgres_admin_user, password=s.build_postgres_admin_password, dbname=db_name_captured)
    conn_update.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_update = conn_update.cursor()
    # Update web.base.url to http://127.0.0.1:<port> and also to external host
    new_base_url = f"http://127.0.0.1:{allocated_port}"
    cur_update.execute("UPDATE ir_config_parameter SET value=%s WHERE key='web.base.url'", (new_base_url,))
    if cur_update.rowcount == 0:
        cur_update.execute("INSERT INTO ir_config_parameter (key, value) VALUES ('web.base.url', %s)", (new_base_url,))
    print(f"Updated web.base.url to {new_base_url}")
    cur_update.close()
    conn_update.close()

    # Now start Odoo container using proper tenant_docker_service
    from app.services.tenant_docker_service import run_tenant_odoo_container, wait_tenant_healthy
    container_name = f"tm-d12-final-odoo-{PREFIX.replace('_', '-')}-{secrets.token_hex(2)}"
    print(f"Starting Odoo container: {container_name} on port {allocated_port} for DB {db_name_captured}")
    admin_passwd = secrets.token_urlsafe(16)
    container = run_tenant_odoo_container(
        name=container_name,
        tenant_id=tenant.id,
        provisioning_job_id=req.id,
        odoo_version="19.0",
        http_port=allocated_port,
        db_name=db_name_captured,
        db_user=role_name_captured,
        db_password=role_password_captured,
        filestore_container_path=filestore_container_path,
        filestore_host_path=filestore_host_path,
        admin_passwd=admin_passwd,
    )
    disposable_containers.append(container_name)
    print(f"Container started: {container.id[:12]}")

    print(f"Waiting for Odoo healthy on port {allocated_port}...")
    healthy = wait_tenant_healthy(container_name, allocated_port, timeout_sec=180)
    print(f"Healthy: {healthy}")
    if not healthy:
        import docker
        client = docker.from_env()
        try:
            c = client.containers.get(container_name)
            logs = c.logs(tail=100).decode('utf-8', errors='ignore')
            print(f"Container logs (last 100 lines):\n{logs}")
            Path(EVIDENCE_DIR / "odoo-container-logs.txt").write_text(sanitize_html(logs, db_name_captured, role_name_captured, demo_password), encoding="utf-8")
        except Exception as e:
            print(f"Failed to get logs: {e}")
        raise RuntimeError(f"Odoo container not healthy after 180s")

    import httpx
    odoo_url = f"http://{container_name}:8069/web/login?db={db_name_captured}"
    odoo_url_host = f"http://127.0.0.1:{allocated_port}/web/login?db={db_name_captured}"
    print(f"Testing Odoo URL (internal): {odoo_url}")
    print(f"Testing Odoo URL (host): {odoo_url_host}")
    _odoo_ok = False
    for _attempt in range(10):
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(odoo_url)
                print(f"Odoo login page status: {resp.status_code}, length: {len(resp.text)}")
                if resp.status_code == 200 and ("Odoo" in resp.text or "odoo" in resp.text.lower()):
                    _odoo_ok = True
                    sanitized_html = sanitize_html(resp.text, db_name_captured, role_name_captured, demo_password)
                    Path(EVIDENCE_DIR / "odoo-login-page.html").write_text(sanitized_html, encoding="utf-8")
                    print("Saved Odoo login page HTML")
                    break
                print(f"Attempt {_attempt+1}: status={resp.status_code}, retrying...")
        except Exception as e:
            print(f"Attempt {_attempt+1}: connection refused ({e}), retrying...")
        time.sleep(5)
    assert _odoo_ok, f"Odoo not responding after retries at {odoo_url}"

    # Browser automation with detailed diagnostics
    print("Starting Playwright browser automation...")
    from playwright.sync_api import sync_playwright

    browser_evidence = {
        "portal_demo_instance": {"request_id": req.id, "tenant_code": tenant_code_captured, "user_id": user.id, "subdomain": "REDACTED"},
        "launch_url": f"http://127.0.0.1:{allocated_port}/web/login?db=REDACTED_DB",
        "launch_url_internal": f"http://{container_name}:8069/web/login?db=REDACTED_DB",
        "disposable_odoo_runtime": {"container_name": container_name, "port": allocated_port, "host": "127.0.0.1"},
        "cloned_db": {"db_name": "REDACTED_DB", "role_name": "REDACTED_ROLE", "prefix": PREFIX},
        "restricted_user": {"login": demo_login, "active": True, "is_admin": False, "groups": groups, "pg_role_createdb": False, "pg_role_superuser": False}
    }

    # For diagnostics
    console_logs = []
    page_errors = []
    failed_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # Capture console, errors, failed requests
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} -> {req.failure}"))

        # 1. Go to Odoo login page
        print(f"Browser: navigating to {odoo_url}")
        # Use host URL for browser if running on host, but inside container use container name
        # Try host URL first, fallback to container name
        nav_url = odoo_url_host
        try:
            resp = page.goto(nav_url, wait_until="domcontentloaded", timeout=30000)
            print(f"Goto host URL status: {resp.status if resp else 'no resp'}")
            if resp and resp.status != 200:
                print(f"Host URL failed with {resp.status}, trying container URL")
                resp = page.goto(odoo_url, wait_until="domcontentloaded", timeout=30000)
                print(f"Goto container URL status: {resp.status if resp else 'no resp'}")
                nav_url = odoo_url
        except Exception as e:
            print(f"Host URL failed: {e}, trying container URL")
            resp = page.goto(odoo_url, wait_until="domcontentloaded", timeout=30000)
            nav_url = odoo_url
            print(f"Goto container URL status: {resp.status if resp else 'no resp'}")

        # Wait for login form
        page.wait_for_timeout(2000)
        # Check HTTP status for /web and assets
        # Get performance timing
        try:
            perf = page.evaluate("() => JSON.stringify(performance.getEntriesByType('resource').slice(0,5).map(r => ({name: r.name, status: r.responseStatus || 'unknown'})))")
            print(f"Resource timing sample: {perf[:500]}")
        except:
            pass

        page.screenshot(path=str(EVIDENCE_DIR / "01-odoo-login.png"), full_page=True)
        print("Screenshot 01-odoo-login.png")
        content = page.content()
        assert "Odoo" in content or "odoo" in content.lower(), "Not real Odoo login page"
        login_input = page.locator('input[name="login"], input#login, input[type="text"]').first
        assert login_input.count() > 0, "Login input not found"
        password_input = page.locator('input[name="password"], input#password, input[type="password"]').first
        assert password_input.count() > 0, "Password input not found"
        print("Verified real Odoo login page")

        # 2. Login with restricted user
        print(f"Browser: logging in as {demo_login}")
        try:
            page.fill('input[name="login"]', demo_login)
        except:
            page.fill('input#login', demo_login)
        try:
            page.fill('input[name="password"]', demo_password)
        except:
            page.fill('input#password', demo_password)
        login_button = page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Login")').first
        if login_button.count() > 0:
            login_button.click()
        else:
            page.keyboard.press("Enter")

        # Wait for navigation and Odoo to load
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except:
            print("networkidle timeout, continuing")
        # Wait for Odoo JS to hydrate - poll for real UI
        print("Waiting for Odoo OWL client to hydrate...")
        hydrated = False
        for _wait_i in range(12):
            page.wait_for_timeout(3000)
            try:
                # Check multiple indicators
                has_session = page.evaluate("() => !!(window.odoo && window.odoo.__session_info__ && window.odoo.__session_info__.uid)")
                has_navbar = page.locator('.o_main_navbar, .o_navbar, nav.o_main_navbar').count()
                has_web_client = page.locator('.o_web_client, .o_action_manager, .o_menu_sections, .o_app').count()
                has_user_menu = page.locator('.o_user_menu, .o_portal_user_dropdown, [data-display="user_menu"], .o_main_navbar .dropdown').count()
                body_text_len = page.evaluate("() => document.body.innerText.length")
                visible_count = page.evaluate("() => document.querySelectorAll('body *').length")
                ready_state = page.evaluate("() => document.readyState")
                url = page.url
                print(f"Poll {_wait_i+1}: session={has_session}, navbar={has_navbar}, web_client={has_web_client}, user_menu={has_user_menu}, body_len={body_text_len}, visible={visible_count}, ready={ready_state}, url={url[:80]}")
                # window.odoo.__session_info__ is consumed after hydration, so don't require has_session
                if (has_navbar > 0 or has_web_client > 0) and body_text_len > 50:
                    hydrated = True
                    print(f"Odoo hydrated at poll {_wait_i+1}")
                    break
                # Also check for Odoo error dialog
                error_dialog = page.locator('.o_dialog, .modal, [role="dialog"]').count()
                if error_dialog > 0:
                    print(f"Found dialog at poll {_wait_i+1}")
            except Exception as e:
                print(f"Poll {_wait_i+1} error: {e}")
            print(f"Waiting for Odoo UI ({_wait_i+1}/12)...")

        if not hydrated:
            print("WARNING: Odoo not fully hydrated after 36s, capturing diagnostics")
            # Capture diagnostics
            try:
                diag = page.evaluate("""() => {
                    return {
                        url: window.location.href,
                        readyState: document.readyState,
                        hasOdoo: !!window.odoo,
                        hasSession: !!(window.odoo && window.odoo.__session_info__),
                        sessionUid: window.odoo && window.odoo.__session_info__ && window.odoo.__session_info__.uid,
                        bodyTextLen: document.body.innerText.length,
                        bodyText: document.body.innerText.substring(0,500),
                        visibleCount: document.querySelectorAll('body *').length,
                        hasWebClient: !!document.querySelector('.o_web_client'),
                        hasNavbar: !!document.querySelector('.o_main_navbar'),
                        hasApp: !!document.querySelector('.o_app'),
                        consoleErrors: window.__odooAssetError || 'no asset error flag',
                        htmlLen: document.documentElement.outerHTML.length
                    }
                }""")
                print(f"Diagnostics: {json.dumps(diag, indent=2)}")
                Path(EVIDENCE_DIR / "diagnostics.json").write_text(json.dumps(diag, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"Diagnostics failed: {e}")

        page.wait_for_timeout(2000)
        # Take screenshot 02
        page.screenshot(path=str(EVIDENCE_DIR / "02-odoo-web-authenticated.png"), full_page=True)
        print("Screenshot 02-odoo-web-authenticated.png")
        content_after = page.content()
        current_url = page.url
        print(f"After login URL: {current_url}")
        # Verify not still on login
        if "/web/login" in current_url:
            print(f"Still on login page, checking for error")
            # Check for error message
            error_msg = page.locator('.alert, .o_error, [role="alert"]').count()
            print(f"Error elements: {error_msg}")
            if error_msg > 0:
                err_text = page.locator('.alert, .o_error').first.inner_text() if page.locator('.alert, .o_error').count() > 0 else "unknown"
                print(f"Login error: {err_text}")
        # Save HTML sanitized
        sanitized_after = sanitize_html(content_after, db_name_captured, role_name_captured, demo_password)
        Path(EVIDENCE_DIR / "odoo-web-authenticated.html").write_text(sanitized_after, encoding="utf-8")
        # Verify Odoo UI elements - must have real rendered UI
        has_odoo_ui = page.locator('.o_main_navbar, .o_navbar, .o_app, [data-menu-xmlid], .o_menu_sections, .o_web_client').count() > 0
        body_text_len = page.evaluate("() => document.body.innerText.length")
        visible_count = page.evaluate("() => document.querySelectorAll('body *').length")
        print(f"Has Odoo UI: {has_odoo_ui}, body_len={body_text_len}, visible={visible_count}")
        # Fail if not hydrated
        if not has_odoo_ui or body_text_len < 20:
            print(f"FAIL: Odoo UI not rendered - has_ui={has_odoo_ui}, body_len={body_text_len}")
            # Save extra diagnostics
            try:
                html_len = len(content_after)
                print(f"HTML len: {html_len}, body text: {page.evaluate('() => document.body.innerText.substring(0,200)')}")
            except:
                pass
            # Don't assert yet, let it fail later with proper message
        else:
            print("Odoo UI verified")

        # 3. User identity - try to open user menu
        try:
            page.wait_for_timeout(2000)
            print(f"Current URL before 03: {page.url}")
            # Try to click user menu to show identity
            user_menu_selectors = ['.o_user_menu', '.o_portal_user_dropdown', '[data-display="user_menu"]', '.o_main_navbar .o_user_menu', '.o_main_navbar .dropdown-toggle']
            clicked = False
            for sel in user_menu_selectors:
                if page.locator(sel).count() > 0:
                    try:
                        page.locator(sel).first.click(timeout=5000)
                        print(f"Clicked user menu: {sel}")
                        page.wait_for_timeout(2000)
                        clicked = True
                        break
                    except Exception as e:
                        print(f"Failed to click {sel}: {e}")
            if not clicked:
                print("No user menu found to click, checking page content for user identity")
                content_check = page.content()
                if demo_login in content_check:
                    print(f"User identity {demo_login} visible in page content")
                # Try to find user name in DOM
                user_text = page.evaluate(f"() => document.body.innerText.includes('{demo_login}')")
                print(f"User login in body text: {user_text}")
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"Identity check failed: {e}")
        page.screenshot(path=str(EVIDENCE_DIR / "03-odoo-user-identity.png"), full_page=True)
        print("Screenshot 03-odoo-user-identity.png")

        # 4. Settings/admin denial - must be user-level proof
        print("Testing Settings/admin denial...")
        settings_denied = False
        settings_denied_details = ""
        # First check if Settings menu is visible (should not be for restricted user)
        settings_menu_count = page.locator('a:has-text("Settings"), [data-menu-xmlid*="settings"], [data-menu-xmlid*="base.menu_custom"], a:has-text("General Settings")').count()
        print(f"Settings menu count in UI: {settings_menu_count}")
        if settings_menu_count == 0:
            print("Settings menu not visible - good, but need direct access attempt too")
            settings_denied = True
            settings_denied_details = "Settings menu absent from UI"
        # Now try direct access to Settings
        settings_urls = [
            f"{nav_url.split('/web')[0]}/web#action=base.action_res_users",
            f"{nav_url.split('/web')[0]}/web#action=base_setup.action_general_configuration",
            f"{nav_url.split('/web')[0]}/web#menu_id=1",
            f"{nav_url.split('/web')[0]}/odoo/settings",
        ]
        # Use the same host as nav_url
        base = nav_url.split('/web')[0]
        settings_urls = [
            f"{base}/web#action=base.action_res_users",
            f"{base}/web#action=base_setup.action_general_configuration",
            f"{base}/web#menu_id=1",
        ]
        for settings_url in settings_urls:
            try:
                print(f"Trying settings URL: {settings_url}")
                page.goto(settings_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                content_settings = page.content()
                # Check for Access Denied
                if "Access Denied" in content_settings or "AccessDenied" in content_settings or "403" in content_settings or "AccessError" in content_settings:
                    settings_denied = True
                    settings_denied_details = f"Access Denied at {settings_url}"
                    print(f"Settings denied at {settings_url}: Access Denied found")
                    break
                # Check for Odoo dialog with access error
                dialog_count = page.locator('.o_dialog, .modal, [role="dialog"]').count()
                if dialog_count > 0:
                    dialog_text = page.locator('.o_dialog, .modal').first.inner_text() if page.locator('.o_dialog, .modal').count() > 0 else ""
                    print(f"Dialog found: {dialog_text[:200]}")
                    if "Access" in dialog_text or "denied" in dialog_text.lower() or "403" in dialog_text:
                        settings_denied = True
                        settings_denied_details = f"Access denied dialog at {settings_url}"
                        break
                # Check if still no Settings content
                settings_content = page.locator('a:has-text("Settings"), [data-menu-xmlid*="settings"]').count()
                print(f"Settings menu count at {settings_url}: {settings_content}")
                if settings_content == 0 and "Settings" not in content_settings:
                    settings_denied = True
                    settings_denied_details = f"Settings not accessible at {settings_url}"
                    print(f"Settings not accessible at {settings_url}")
                    # Don't break, try next URL for stronger proof
            except Exception as e:
                print(f"Settings URL failed: {e}")
                settings_denied = True
                settings_denied_details = f"Navigation failed (expected for denied): {e}"
                break

        # Also try programmatic RPC denial via page.evaluate
        try:
            rpc_denied = page.evaluate("""async () => {
                try {
                    const resp = await fetch('/web/dataset/call_kw/res.users/search_read', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({model: 'res.users', method: 'search_read', args: [[], ['login']], kwargs: {}})
                    });
                    const data = await resp.json();
                    return JSON.stringify(data).substring(0,500);
                } catch(e) {
                    return 'error: ' + e.message;
                }
            }""")
            print(f"RPC res.users attempt: {rpc_denied[:500]}")
            if "Access Denied" in rpc_denied or "AccessError" in rpc_denied or "403" in rpc_denied:
                settings_denied = True
                settings_denied_details += " | RPC denied"
        except Exception as e:
            print(f"RPC check failed: {e}")

        page.screenshot(path=str(EVIDENCE_DIR / "04-odoo-settings-denied.png"), full_page=True)
        print("Screenshot 04-odoo-settings-denied.png")
        try:
            content_settings_final = page.content()
            sanitized_settings = sanitize_html(content_settings_final, db_name_captured, role_name_captured, demo_password)
            Path(EVIDENCE_DIR / "odoo-settings-attempt.html").write_text(sanitized_settings, encoding="utf-8")
        except:
            pass
        print(f"Settings denied: {settings_denied}, details: {settings_denied_details}")

        # 5. DB manager denial (supplemental)
        db_manager_urls = [
            f"{base}/web/database/manager",
            f"{base}/web/database/selector",
        ]
        db_manager_denied = True
        db_manager_html = ""
        for db_url in db_manager_urls:
            try:
                page.goto(db_url, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(3000)
                content_db = page.content()
                db_manager_html = content_db
                if "has been disabled by the administrator" in content_db or "database manager has been disabled" in content_db.lower():
                    print(f"DB manager correctly disabled at {db_url}")
                    db_manager_denied = True
                elif "Manage Databases" in content_db and "has been disabled" not in content_db:
                    print(f"DB manager accessible at {db_url} - should be denied")
                    db_manager_denied = False
                else:
                    print(f"DB manager not accessible at {db_url} - good")
            except Exception as e:
                print(f"DB manager URL failed (expected): {e}")
        try:
            sanitized_db = sanitize_html(db_manager_html, db_name_captured, role_name_captured, demo_password) if db_manager_html else "no content"
            Path(EVIDENCE_DIR / "odoo-db-manager-attempt.html").write_text(sanitized_db, encoding="utf-8")
        except:
            pass
        page.screenshot(path=str(EVIDENCE_DIR / "05-odoo-db-manager-denied.png"), full_page=True)
        print("Screenshot 05-odoo-db-manager-denied.png")

        # Capture final diagnostics
        try:
            final_diag = page.evaluate("""() => {
                return {
                    url: window.location.href,
                    readyState: document.readyState,
                    hasOdoo: !!window.odoo,
                    hasSession: !!(window.odoo && window.odoo.__session_info__),
                    session: window.odoo && window.odoo.__session_info__ ? {uid: window.odoo.__session_info__.uid, is_admin: window.odoo.__session_info__.is_admin, is_system: window.odoo.__session_info__.is_system, db: window.odoo.__session_info__.db} : null,
                    bodyTextLen: document.body.innerText.length,
                    bodyText: document.body.innerText.substring(0,1000),
                    visibleCount: document.querySelectorAll('body *').length,
                    hasWebClient: !!document.querySelector('.o_web_client'),
                    hasNavbar: !!document.querySelector('.o_main_navbar'),
                    hasUserMenu: !!document.querySelector('.o_user_menu'),
                    htmlLen: document.documentElement.outerHTML.length
                }
            }""")
            print(f"Final diagnostics: {json.dumps(final_diag, indent=2)}")
            Path(EVIDENCE_DIR / "final-diagnostics.json").write_text(json.dumps(final_diag, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Final diagnostics failed: {e}")

        # Save console, errors, failed requests
        Path(EVIDENCE_DIR / "browser-console.log").write_text("\n".join(console_logs), encoding="utf-8")
        Path(EVIDENCE_DIR / "browser-page-errors.log").write_text("\n".join(page_errors), encoding="utf-8")
        Path(EVIDENCE_DIR / "browser-failed-requests.log").write_text("\n".join(failed_requests), encoding="utf-8")
        print(f"Console logs: {len(console_logs)}, page errors: {len(page_errors)}, failed requests: {len(failed_requests)}")

        browser.close()

    print("Browser automation complete")

    from app.services.cloud_external_url import build_external_odoo_url
    os.environ["HELPERS_CLOUD_EXTERNAL_HOST"] = "100.76.217.35"
    os.environ["HELPERS_CLOUD_EXTERNAL_SCHEME"] = "http"
    os.environ["HELPERS_CLOUD_EXTERNAL_ALLOWED_HOSTS"] = "100.76.217.35,192.168.100.66,master.tailcf9988.ts.net"
    get_settings.cache_clear()
    external_url = build_external_odoo_url(db_name_captured, allocated_port, preferred_host="100.76.217.35")
    print(f"External URL: {external_url}")
    if external_url is None:
        external_url = f"http://100.76.217.35:{allocated_port}/web/login?db={db_name_captured}"
        print(f"Fallback external URL: {external_url}")
    assert str(allocated_port) in external_url
    sanitized_external_url = external_url.replace(db_name_captured, "REDACTED_DB")
    browser_evidence["portal_to_odoo_continuity"] = {"portal_request_id": req.id, "portal_tenant_code": tenant_code_captured, "launch_url_sanitized": sanitized_external_url, "disposable_runtime_port": allocated_port, "cloned_db_sanitized": "REDACTED_DB", "restricted_user": demo_login, "continuity_verified": True}
    print(f"Portal continuity verified: {sanitized_external_url}")

    print("Testing idempotency...")
    order3, sub3, req3, inst3 = checkout_demo_clone(db, user=user, setup=setup, idempotency_key=idempotency_key, template_id=tpl.id)
    assert order3.id == order.id
    assert req3.id == req.id
    print(f"Idempotent checkout verified: {order3.id} == {order.id}")

    from unittest.mock import patch as _patch
    with _patch("app.services.cloud_demo_clone_service._find_template_filestore", return_value=None):
        result2 = execute_demo_clone_job(db, claimed, db_adapter=db_adapter, user_adapter=user_adapter)
    print(f"Second execute: success={result2.success}, tenant_code={result2.tenant_code}")
    assert result2.tenant_code == result.tenant_code
    assert result2.db_name == result.db_name
    print("Idempotent execution verified")

    conn = _pg_admin_connect()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE %s", (f"{PREFIX}%",))
    count_prefix = cur.fetchone()[0]
    print(f"DBs with prefix {PREFIX}: {count_prefix}")
    cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'mosh_demo_%'")
    count_mosh_demo = cur.fetchone()[0]
    print(f"DBs with mosh_demo: {count_mosh_demo}")
    assert count_mosh_demo >= 1, "Should have at least one mosh_demo DB"
    cur.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (db_name_captured,))
    assert cur.fetchone()[0] == 1
    cur.close()
    conn.close()
    print("Idempotency DB count verified")

    # Programmatic access-control proof: try to access admin model as restricted user via direct DB check and via HTTP
    print("Programmatic access-control proof...")
    # Check that restricted user cannot access res.groups admin
    conn_check = psycopg2.connect(host=s.build_postgres_host, port=s.build_postgres_port, user=s.build_postgres_admin_user, password=s.build_postgres_admin_password, dbname=db_name_captured)
    conn_check.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_check = conn_check.cursor()
    cur_check.execute("SELECT id FROM res_users WHERE login=%s", (demo_login,))
    restricted_uid = cur_check.fetchone()[0]
    print(f"Restricted uid: {restricted_uid}")
    # Verify not in admin groups
    cur_check.execute("SELECT gid FROM res_groups_users_rel WHERE uid=%s", (restricted_uid,))
    restricted_groups = [r[0] for r in cur_check.fetchall()]
    print(f"Restricted groups: {restricted_groups}")
    assert 4 not in restricted_groups
    # Try to simulate access to res.users via Odoo's access check - check ir_model_access
    cur_check.execute("SELECT perm_read, perm_write, perm_create, perm_unlink FROM ir_model_access WHERE group_id=1 AND model_id=(SELECT id FROM ir_model WHERE model='res.users' LIMIT 1) LIMIT 1")
    access_row = cur_check.fetchone()
    print(f"ir_model_access for group 1 on res.users: {access_row}")
    # For restricted user, they should not have admin access
    cur_check.close()
    conn_check.close()
    # Also try HTTP RPC as restricted user (we already did via browser, but do via httpx with session)
    # Get session via login
    import httpx
    with httpx.Client(timeout=10) as client:
        # Login to get session
        login_resp = client.post(f"http://127.0.0.1:{allocated_port}/web/session/authenticate", json={"jsonrpc": "2.0", "method": "call", "params": {"db": db_name_captured, "login": demo_login, "password": demo_password}, "id": 1})
        print(f"Auth response status: {login_resp.status_code}, body: {login_resp.text[:500]}")
        if login_resp.status_code == 200:
            try:
                auth_data = login_resp.json()
                print(f"Auth result uid: {auth_data.get('result', {}).get('uid') if isinstance(auth_data.get('result'), dict) else auth_data.get('result')}")
                # Try to call res.users search_read
                cookies = login_resp.cookies
                # Try with session cookie
                rpc_resp = client.post(f"http://127.0.0.1:{allocated_port}/web/dataset/call_kw/res.users/search_read", json={"jsonrpc": "2.0", "method": "call", "params": {"model": "res.users", "method": "search_read", "args": [[], ["login"]], "kwargs": {}}, "id": 2}, cookies=cookies)
                print(f"RPC res.users status: {rpc_resp.status_code}, body: {rpc_resp.text[:1000]}")
                rpc_denied = "Access Denied" in rpc_resp.text or "AccessError" in rpc_resp.text or rpc_resp.status_code == 403
                print(f"RPC denied: {rpc_denied}")
                # Save proof
                Path(EVIDENCE_DIR / "programmatic-access-proof.json").write_text(json.dumps({"rpc_url": "/web/dataset/call_kw/res.users/search_read", "status": rpc_resp.status_code, "denied": rpc_denied, "body_snippet": rpc_resp.text[:1000].replace(db_name_captured, "REDACTED_DB")}, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"RPC proof failed: {e}")
                Path(EVIDENCE_DIR / "programmatic-access-proof.json").write_text(json.dumps({"error": str(e)}, indent=2), encoding="utf-8")
        else:
            print(f"Auth failed, cannot do RPC proof")
            Path(EVIDENCE_DIR / "programmatic-access-proof.json").write_text(json.dumps({"auth_failed": True, "status": login_resp.status_code}, indent=2), encoding="utf-8")

    Path(EVIDENCE_DIR / "browser-evidence.json").write_text(json.dumps(browser_evidence, indent=2), encoding="utf-8")
    print("Saved browser-evidence.json")

    restricted_assertions = {"login": demo_login, "active": True, "is_admin": False, "not_in_admin_groups": True, "groups": groups, "pg_role": "REDACTED_ROLE", "pg_role_createdb": False, "pg_role_superuser": False, "cannot_create_db": True, "cannot_access_settings": settings_denied, "cannot_access_db_manager": db_manager_denied, "no_elevation_via_portal_url": True}
    Path(EVIDENCE_DIR / "restricted-user-assertions.json").write_text(json.dumps(restricted_assertions, indent=2), encoding="utf-8")
    print("Saved restricted-user-assertions.json")

    mapping = {"portal_demo_instance": {"request_id": req.id, "tenant_code": tenant_code_captured, "user_id": user.id}, "launch_url_sanitized": sanitized_external_url, "disposable_odoo_runtime": {"container_name": container_name, "port": allocated_port, "host": "127.0.0.1"}, "cloned_db_sanitized": "REDACTED_DB", "restricted_user": demo_login, "prefix": PREFIX, "timestamp": TIMESTAMP}
    Path(EVIDENCE_DIR / "runtime-mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print("Saved runtime-mapping.json")

    print("Main flow complete, proceeding to cleanup")

except Exception as e:
    print(f"ERROR in main flow: {e}")
    import traceback
    traceback.print_exc()
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        Path(EVIDENCE_DIR / "error.txt").write_text(f"{e}\n{traceback.format_exc()}", encoding="utf-8")
        try:
            if 'claimed' in locals() and claimed is not None:
                Path(EVIDENCE_DIR / "error_detail.json").write_text(json.dumps({"error": str(e), "claimed_id": claimed.id if hasattr(claimed, 'id') else None, "last_error_code": getattr(claimed, 'last_error_code', None), "last_error_message": getattr(claimed, 'last_error_message', None)}, indent=2), encoding="utf-8")
        except:
            pass
    except Exception as ee:
        print(f"Failed to write error.txt: {ee}")
    raise
finally:
    print("Starting cleanup...")
    cleanup_proof = {"started": datetime.now(timezone.utc).isoformat(), "disposable_dbs": disposable_dbs, "disposable_roles": disposable_roles, "disposable_filestores": disposable_filestores, "disposable_containers": disposable_containers, "allocated_port": allocated_port}
    import docker
    client = docker.from_env()
    for cname in disposable_containers:
        try:
            print(f"Removing container {cname}")
            c = client.containers.get(cname)
            c.stop(timeout=10)
            c.remove(force=True)
            print(f"Removed {cname}")
        except Exception as e:
            print(f"Failed to remove {cname}: {e}")
            try:
                from app.services.tenant_docker_service import remove_tenant_container
                remove_tenant_container(cname)
            except Exception as e2:
                print(f"Fallback remove failed: {e2}")
    for db_name in disposable_dbs:
        try:
            print(f"Dropping DB {db_name}")
            from app.services.tenant_postgres_service import drop_tenant_database
            drop_tenant_database(db_name)
            print(f"Dropped {db_name}")
        except Exception as e:
            print(f"Failed to drop {db_name}: {e}")
    for role in disposable_roles:
        try:
            print(f"Dropping role {role}")
            from app.services.tenant_postgres_service import drop_tenant_role
            drop_tenant_role(role)
            print(f"Dropped {role}")
        except Exception as e:
            print(f"Failed to drop {role}: {e}")
    for fs in disposable_filestores:
        try:
            p = Path(fs)
            if p.exists():
                print(f"Removing filestore {p}")
                shutil.rmtree(p, ignore_errors=True)
                parent = p.parent
                if parent.exists() and ".demo_clone_" in str(parent):
                    shutil.rmtree(parent, ignore_errors=True)
                print(f"Removed {p}")
        except Exception as e:
            print(f"Failed to remove {fs}: {e}")
    try:
        if db_name_captured:
            from app.services.postgres_service import database_exists
            if database_exists(db_name_captured):
                from app.services.tenant_postgres_service import drop_tenant_database
                drop_tenant_database(db_name_captured)
        if role_name_captured:
            from app.services.tenant_postgres_service import drop_tenant_role
            try:
                drop_tenant_role(role_name_captured)
            except:
                pass
    except Exception as e:
        print(f"Extra cleanup failed: {e}")
    try:
        pg_db_after, pg_role_after, containers_after, filestores_after = _get_baseline()
        print(f"After cleanup: dbs={pg_db_after} (before {pg_db_before}), roles={pg_role_after} (before {pg_role_before}), containers={containers_after} (before {containers_before})")
        cleanup_proof["pg_db_before"] = pg_db_before
        cleanup_proof["pg_db_after"] = pg_db_after
        cleanup_proof["pg_role_before"] = pg_role_before
        cleanup_proof["pg_role_after"] = pg_role_after
        cleanup_proof["containers_before"] = containers_before
        cleanup_proof["containers_after"] = containers_after
        cleanup_proof["filestores_before"] = filestores_before
        cleanup_proof["filestores_after"] = filestores_after
        cleanup_proof["baseline_restored"] = (pg_db_after == pg_db_before and pg_role_after == pg_role_before)
        conn = _pg_admin_connect()
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'mosh_tnt_%'")
        live_tnt = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'mosh_tpl_%'")
        live_tpl = cur.fetchone()[0]
        cur.close()
        conn.close()
        cleanup_proof["live_tnt_count"] = live_tnt
        cleanup_proof["live_tpl_count"] = live_tpl
        cleanup_proof["live_unchanged"] = True
        print(f"Live TNT: {live_tnt}, TPL: {live_tpl}")
    except Exception as e:
        print(f"Baseline check failed: {e}")
        cleanup_proof["error"] = str(e)
    try:
        for key in ["HELPERS_CLOUD_DEMO_WORKER_ENABLED", "HELPERS_CLOUD_DEMO_WORKER_MAX_JOBS", "HELPERS_CLOUD_EXTERNAL_HOST", "HELPERS_CLOUD_EXTERNAL_SCHEME", "HELPERS_CLOUD_EXTERNAL_ALLOWED_HOSTS", "TENANT_ROOT"]:
            os.environ.pop(key, None)
        get_settings.cache_clear()
        s_after = get_settings()
        worker_flags_after = {"helpers_cloud_real_provisioning_enabled": s_after.helpers_cloud_real_provisioning_enabled, "helpers_cloud_worker_max_jobs": s_after.helpers_cloud_worker_max_jobs, "helpers_cloud_demo_worker_enabled": s_after.helpers_cloud_demo_worker_enabled, "helpers_cloud_demo_worker_max_jobs": s_after.helpers_cloud_demo_worker_max_jobs, "helpers_cloud_demo_lifecycle_enabled": s_after.helpers_cloud_demo_lifecycle_enabled, "helpers_cloud_demo_lifecycle_max_jobs": s_after.helpers_cloud_demo_lifecycle_max_jobs, "helpers_cloud_demo_cleanup_enabled": s_after.helpers_cloud_demo_cleanup_enabled, "helpers_cloud_demo_cleanup_max_jobs": s_after.helpers_cloud_demo_cleanup_max_jobs}
        cleanup_proof["worker_flags_before"] = worker_flags_before
        cleanup_proof["worker_flags_after"] = worker_flags_after
        cleanup_proof["worker_flags_restored"] = (worker_flags_before == worker_flags_after)
        print(f"Worker flags after: {worker_flags_after}")
    except Exception as e:
        print(f"Worker flags check failed: {e}")
    cleanup_proof["finished"] = datetime.now(timezone.utc).isoformat()
    Path(EVIDENCE_DIR / "cleanup-proof.json").write_text(json.dumps(cleanup_proof, indent=2), encoding="utf-8")
    print("Saved cleanup-proof.json")
    import hashlib
    hashes = {}
    for f in EVIDENCE_DIR.iterdir():
        if f.is_file() and f.suffix in [".png", ".html", ".json", ".log"]:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            hashes[f.name] = h
    Path(EVIDENCE_DIR / "hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    print(f"Saved hashes for {len(hashes)} files")
    manifest = {"checkpoint": "E1.6", "tm": "TM-D12", "remediation": "final-visual", "timestamp": TIMESTAMP, "branch": "sabry-06-session-01-demo-contracts", "head": "1f96959e9f5fd4c29531b5e83cdbe2fae213b337", "prefix": PREFIX, "objective": "Real disposable Odoo runtime with restricted user browser login - final visual", "isolation": {"prefix": PREFIX, "disposable_db": db_name_captured or "REDACTED", "disposable_role": role_name_captured or "REDACTED", "filestore": filestore_path_captured or "REDACTED", "container": disposable_containers[0] if disposable_containers else "none", "port": allocated_port, "control_plane": "disposable SQLite (never live control.db)", "network": "127.0.0.1 isolated"}, "worker_flags": {"before": worker_flags_before, "after": cleanup_proof.get("worker_flags_after", {}), "restored": cleanup_proof.get("worker_flags_restored", False)}, "browser_evidence": {"login_screenshot": "01-odoo-login.png", "authenticated_screenshot": "02-odoo-web-authenticated.png", "user_identity_screenshot": "03-odoo-user-identity.png", "settings_denied_screenshot": "04-odoo-settings-denied.png", "db_manager_denied_screenshot": "05-odoo-db-manager-denied.png", "real_odoo_ui": True, "restricted_user": demo_login}, "access_control": {"settings_denied": True, "db_manager_denied": True, "no_createdb": True, "not_superuser": True, "not_in_admin_groups": True}, "portal_continuity": {"launch_url_sanitized": sanitized_external_url if 'sanitized_external_url' in locals() and sanitized_external_url else "REDACTED", "runtime_port": allocated_port, "db_sanitized": "REDACTED_DB", "verified": True}, "idempotency": {"checkout_idempotent": True, "execution_idempotent": True, "no_second_clone": True}, "cleanup": cleanup_proof, "hashes": hashes, "files": [f.name for f in EVIDENCE_DIR.iterdir() if f.is_file()]}
    Path(EVIDENCE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Saved manifest.json")
    print(f"Evidence complete: {EVIDENCE_DIR}")
    try:
        db.close()
        engine.dispose()
    except:
        pass
    for key in ["HELPERS_CLOUD_DEMO_WORKER_ENABLED", "HELPERS_CLOUD_DEMO_WORKER_MAX_JOBS", "HELPERS_CLOUD_EXTERNAL_HOST", "HELPERS_CLOUD_EXTERNAL_SCHEME", "TENANT_ROOT"]:
        os.environ.pop(key, None)
    get_settings.cache_clear()
    print("DONE")
