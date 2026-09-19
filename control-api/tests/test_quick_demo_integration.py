from __future__ import annotations
import re
import pytest
from app.config import get_settings
from app.dependencies import get_current_user
from app.models import QuickDemoSession, User
from app.services.quick_demo_fake_adapter import FakeQuickDemoRuntime
from app.services.quick_demo_service import run_fake_or_runtime

@pytest.fixture
def qd_user(db):
    u=User(github_id="http-qd",github_login="http-qd",name="HTTP QD",auth_provider="github")
    db.add(u);db.commit();db.refresh(u);return u

@pytest.fixture
def enabled(monkeypatch):
    for k,v in {"QUICK_DEMO_ENABLED":"true","QUICK_DEMO_COMMUNITY_HMS_ENABLED":"true",
        "QUICK_DEMO_MAX_ACTIVE_SESSIONS":"2","QUICK_DEMO_PUBLIC_BASE_DOMAIN":"demo.example.test",
        "QUICK_DEMO_ADAPTER":"fake"}.items(): monkeypatch.setenv(k,v)
    get_settings.cache_clear();return get_settings()

@pytest.fixture
def authed_client(client,qd_user):
    from app.main import app
    app.dependency_overrides[get_current_user]=lambda:qd_user
    yield client
    app.dependency_overrides.pop(get_current_user,None)

def _csrf(text):
    match=re.search(r'name="csrf_token" value="([^"]+)"',text);assert match;return match.group(1)

def test_auth_and_csrf_required(client,enabled):
    assert client.get("/quick-demo/hms").status_code==401

def test_html_json_fake_flow_and_private_headers(authed_client,db,enabled):
    start=authed_client.get("/quick-demo/hms?lang=ar")
    assert start.status_code==200 and 'name="language" value="ar"' in start.text
    token=_csrf(start.text)
    assert authed_client.post("/quick-demo/sessions",data={"solution_code":"hms","edition":"community","language":"ar","csrf_token":"bad"}).status_code==403
    payload={"solution_code":"hms","edition":"community","language":"ar","csrf_token":token}
    one=authed_client.post("/quick-demo/sessions",data=payload,follow_redirects=False)
    two=authed_client.post("/quick-demo/sessions",data=payload,follow_redirects=False)
    assert one.status_code==two.status_code==303 and one.headers["location"]==two.headers["location"]
    status_path=one.headers["location"].split("?",1)[0]
    html=authed_client.get(status_path,headers={"Accept":"text/html"})
    assert html.status_code==200 and "text/html" in html.headers["content-type"]
    assert html.headers["cache-control"]=="no-store" and "Accept" in html.headers["vary"]
    js=authed_client.get(status_path,headers={"Accept":"application/json"})
    assert js.status_code==200 and js.headers["content-type"].startswith("application/json")
    assert js.json()["state"]=="requested" and "database_identifier" not in js.text
    assert authed_client.get(status_path.replace("/status/","/open/"),follow_redirects=False).status_code==409
    item=db.query(QuickDemoSession).one();run_fake_or_runtime(db,item.public_id,"http-worker",FakeQuickDemoRuntime(),enabled)
    ready=authed_client.get(status_path,headers={"Accept":"application/json"}).json()
    assert ready["state"]=="active" and ready["can_launch"] and ready["open_href"]
    opened=authed_client.get(ready["open_href"],follow_redirects=False)
    assert opened.status_code==303 and opened.headers["location"].startswith("https://")

def test_catalog_flag_contract(client,monkeypatch):
    monkeypatch.setenv("QUICK_DEMO_ENABLED","false");monkeypatch.setenv("QUICK_DEMO_COMMUNITY_HMS_ENABLED","false");get_settings.cache_clear()
    off=client.get("/catalog");assert off.status_code==200 and "data-cta-quick-demo" not in off.text
    monkeypatch.setenv("QUICK_DEMO_ENABLED","true");monkeypatch.setenv("QUICK_DEMO_COMMUNITY_HMS_ENABLED","true");get_settings.cache_clear()
    on=client.get("/catalog");assert on.status_code==200
    assert "data-cta-quick-demo" in on.text and "data-cta-trial" in on.text and "data-cta-paid" in on.text
    assert "edition=enterprise" not in on.text
