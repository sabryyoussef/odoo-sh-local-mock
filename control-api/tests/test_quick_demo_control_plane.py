from __future__ import annotations
import re
from datetime import timedelta
import pytest
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.models import QuickDemoAuditEvent, QuickDemoSession, User
from app.services.quick_demo_fake_adapter import FakeQuickDemoRuntime
from app.services.quick_demo_service import (
    QuickDemoError, claim, create_session, finish_cleanup, owned_session, renew_lease,
    request_cleanup, run_fake_or_runtime, status_context, utcnow,
)

def _enable(monkeypatch, capacity=2):
    values={
        "QUICK_DEMO_ENABLED":"true","QUICK_DEMO_COMMUNITY_HMS_ENABLED":"true",
        "QUICK_DEMO_MAX_ACTIVE_SESSIONS":str(capacity),"QUICK_DEMO_PUBLIC_BASE_DOMAIN":"demo.example.test",
        "QUICK_DEMO_ADAPTER":"fake","QUICK_DEMO_AUTH_REQUIRED":"true","QUICK_DEMO_ALLOW_ANONYMOUS":"false",
    }
    for k,v in values.items(): monkeypatch.setenv(k,v)
    get_settings.cache_clear(); return get_settings()

@pytest.fixture
def user(db):
    value=User(github_id="qd1-user",github_login="qd1-user",name="QD User",auth_provider="github")
    db.add(value);db.commit();db.refresh(value);return value

def _create(db,settings,user,secret="browser",nonce="nonce"):
    return create_session(db,settings,browser_secret=secret,request_nonce=nonce,owner_user_id=user.id,
        solution_code="hms",edition="community",language="en")

def test_defaults_fail_closed(db,user,monkeypatch):
    for name in ("QUICK_DEMO_ENABLED","QUICK_DEMO_COMMUNITY_HMS_ENABLED"): monkeypatch.delenv(name,raising=False)
    get_settings.cache_clear()
    with pytest.raises(QuickDemoError) as exc:_create(db,get_settings(),user)
    assert exc.value.status==404
    settings=_enable(monkeypatch,0)
    with pytest.raises(QuickDemoError) as exc:_create(db,settings,user)
    assert exc.value.status==503
    assert settings.quick_demo_absolute_ttl_minutes==240
    assert settings.quick_demo_idle_timeout_minutes==30
    assert not settings.quick_demo_cron_enabled and not settings.quick_demo_outbound_integrations_enabled

def test_schema_and_routes(isolated_app_db):
    columns={c["name"] for c in inspect(isolated_app_db).get_columns("quick_demo_sessions")}
    assert {"language","idle_expires_at","runtime_slot","allocation_id","container_ownership",
            "database_ownership","role_ownership","filestore_ownership","route_ownership",
            "adapter_name","lease_token","lease_expires_at"} <= columns
    unique_indexes={i["name"] for i in inspect(isolated_app_db).get_indexes("quick_demo_sessions") if i["unique"]}
    assert {"uq_quick_demo_runtime_slot","uq_quick_demo_allocation_id"} <= unique_indexes
    from app.main import app
    paths={r.path for r in app.routes}
    assert {"/quick-demo/hms","/quick-demo/sessions","/quick-demo/status/{public_id}",
            "/quick-demo/open/{public_id}","/portal/trial/confirm","/cloud/setup/confirm"} <= paths

def test_auth_ownership_idempotency_capacity(db,user,monkeypatch):
    settings=_enable(monkeypatch,1)
    with pytest.raises(QuickDemoError):
        create_session(db,settings,browser_secret="x",request_nonce="x",owner_user_id=None,
                       solution_code="hms",edition="community",language="en")
    item=_create(db,settings,user)
    assert _create(db,settings,user).id==item.id
    assert item.runtime_slot==1 and not item.public_id.isdigit()
    with pytest.raises(QuickDemoError): owned_session(db,item.public_id,"wrong",user.id)
    other=User(github_id="other",github_login="other",auth_provider="github");db.add(other);db.commit()
    with pytest.raises(QuickDemoError): owned_session(db,item.public_id,"browser",other.id)
    with pytest.raises(QuickDemoError) as exc:create_session(db,settings,browser_secret="other",request_nonce="other",
        owner_user_id=other.id,solution_code="hms",edition="community",language="en")
    assert exc.value.status==503

def test_fake_flow_lease_expiry_cleanup(db,user,monkeypatch):
    settings=_enable(monkeypatch);item=_create(db,settings,user)
    assert claim(db,item.public_id,"w1");db.refresh(item);token=item.lease_token
    assert not claim(db,item.public_id,"w2")
    assert renew_lease(db,item,"w1",token)
    assert not renew_lease(db,item,"w1","stale-token")
    item.lease_expires_at=utcnow()-timedelta(seconds=1);db.commit()
    fake=FakeQuickDemoRuntime();run_fake_or_runtime(db,item.public_id,"w2",fake,settings);db.refresh(item)
    assert item.state=="active" and item.expires_at-item.activated_at==timedelta(minutes=240)
    assert item.idle_expires_at-item.activated_at==timedelta(minutes=30)
    ctx=status_context(db,item,settings)
    assert ctx["can_launch"] and ctx["open_href"]
    assert item.database_identifier not in str(ctx) and "lease_token" not in str(ctx)
    item.idle_expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert status_context(db,item,settings)["state"]=="expired"
    assert request_cleanup(db,item);assert not request_cleanup(db,item)
    finish_cleanup(db,item,fake);finish_cleanup(db,item,fake);db.refresh(item)
    assert item.state=="deleted" and item.runtime_slot is None and fake.cleanups==[item.public_id]
    events={e.event_type for e in db.scalars(select(QuickDemoAuditEvent).where(QuickDemoAuditEvent.session_id==item.id))}
    assert {"created","transition","activated","expired","cleanup_intent","cleaned"}<=events

def test_fake_only_and_sanitized_failure(db,user,monkeypatch):
    settings=_enable(monkeypatch);item=_create(db,settings,user)
    class Bad(FakeQuickDemoRuntime):
        def clone_database(self,session): raise RuntimeError("password=secret host=internal")
    with pytest.raises(RuntimeError):run_fake_or_runtime(db,item.public_id,"worker",Bad(),settings)
    db.refresh(item);ctx=status_context(db,item,settings)
    assert item.state=="failed" and "secret" not in str(ctx) and "internal" not in str(ctx)
    monkeypatch.setenv("QUICK_DEMO_ADAPTER","real");get_settings.cache_clear()
    with pytest.raises(QuickDemoError) as exc:run_fake_or_runtime(db,item.public_id,"worker",FakeQuickDemoRuntime(),get_settings())
    assert exc.value.code=="adapter_disabled"

def test_trusted_launch_validation(db,user,monkeypatch):
    settings=_enable(monkeypatch);item=_create(db,settings,user);fake=FakeQuickDemoRuntime()
    run_fake_or_runtime(db,item.public_id,"worker",fake,settings);db.refresh(item)
    assert status_context(db,item,settings)["can_launch"]
    item.public_url="http://"+item.route_hostname+"/web";db.commit()
    assert not status_context(db,item,settings)["can_launch"]
    item.public_url="https://evil.example.test/web";db.commit()
    assert not status_context(db,item,settings)["can_launch"]

def test_atomic_capacity_under_concurrency(tmp_path,monkeypatch):
    from app.db import Base
    settings=_enable(monkeypatch,1)
    engine=create_engine(f"sqlite:///{tmp_path/'capacity.db'}",connect_args={"check_same_thread":False,"timeout":10})
    Base.metadata.create_all(engine);Factory=sessionmaker(bind=engine,expire_on_commit=False)
    with Factory() as db:
        db.add_all([User(id=901,github_id="c901",auth_provider="github"),User(id=902,github_id="c902",auth_provider="github")]);db.commit()
    def attempt(uid):
        with Factory() as db:
            try:
                return create_session(db,settings,browser_secret=f"b{uid}",request_nonce="n",owner_user_id=uid,
                    solution_code="hms",edition="community",language="en").public_id
            except QuickDemoError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,(901,902)))
    assert sum(value!="capacity_unavailable" for value in results)==1
    with Factory() as db:
        assert db.query(QuickDemoSession).filter(QuickDemoSession.runtime_slot.is_not(None)).count()==1
    engine.dispose()
