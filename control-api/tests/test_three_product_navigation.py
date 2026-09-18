"""Homepage and nav present three independent Helpers ERP product lines."""

from __future__ import annotations


def test_homepage_shows_three_product_lines(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Ready Solutions" in body
    assert "Helpers ERP Cloud" in body
    assert "Developer Platform" in body
    assert 'href="/solutions"' in body
    assert 'href="/cloud"' in body
    assert 'href="/platform"' in body
    assert "Browse Ready Solutions" in body
    assert "Build Your Odoo Cloud" in body
    assert "Try Demo" in body
    assert 'href="/cloud/build"' in body
    assert 'href="/cloud/demo"' in body
    assert "Open Developer Platform" in body


def test_homepage_ctas_open_correct_journeys(client, db):
    from app.services.catalog_service import seed_demo_catalog
    from app.services.cloud_catalog_service import seed_helpers_cloud

    seed_demo_catalog(db)
    seed_helpers_cloud(db)
    solutions = client.get("/solutions", follow_redirects=True)
    assert solutions.status_code == 200
    assert "Veterinary Hospital" in solutions.text
    cloud = client.get("/cloud")
    assert cloud.status_code == 200
    assert "Helpers ERP Cloud" in cloud.text
    assert "Build Your Odoo Cloud" in cloud.text
    assert "Try Demo" in cloud.text
    assert "How the paid service works" in cloud.text
    assert 'href="/cloud/login"' in cloud.text
    platform = client.get("/platform")
    assert platform.status_code == 200
    assert "Developer Platform" in platform.text
    assert "GitHub" in platform.text
    assert 'href="/login"' in platform.text


def test_contextual_sign_in_targets(client):
    home = client.get("/").text
    assert 'href="/cloud/login"' in home
    assert "Cloud sign in" in home
    assert "Developer sign in" in home
    cloud = client.get("/cloud").text
    assert 'href="/cloud/login"' in cloud
    assert "Cloud Sign In" in cloud
    platform = client.get("/platform").text
    actions = platform.split("mkt-nav__actions", 1)[-1]
    assert 'href="/login"' in actions
    assert 'href="/cloud/login"' not in actions


def test_nav_lists_three_product_lines_and_sign_in(client):
    body = client.get("/").text
    assert "Ready Solutions" in body
    assert "Helpers ERP Cloud" in body
    assert "Developer Platform" in body
    assert "Pricing" in body
    assert "Cloud sign in" in body
    assert "Developer sign in" in body
    assert 'href="/cloud/login"' in body
    assert 'href="/login"' in body


def test_product_positioning_not_mixed_on_cloud_overview(client):
    body = client.get("/cloud").text
    assert "commit" not in body.lower()
    assert "branch" not in body.lower()
    assert "CONNECT" not in body
    assert "Build your company Odoo cloud with a clear monthly total" in body
    assert "Build Your Odoo Cloud" in body
    assert "Try Demo" in body
    assert "How the paid service works" in body
    assert "View Plans" not in body
    assert "not custom code" not in body.lower()
    assert "not a developer build environment" not in body.lower()


def test_developer_platform_keeps_git_language(client):
    body = client.get("/platform").text
    assert "GitHub" in body
    assert "builds" in body.lower()
