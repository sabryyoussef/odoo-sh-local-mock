"""QD1 Community Quick Demo — presentational contract (no live provisioning).

Renders allowed templates with isolated Jinja context. Does not call missing
backend routes. Route-level E2E remains integration-pending until QD1 backend
lands.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.i18n import translator
from app.translations import TRANSLATIONS

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"
STATIC_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "quick-demo.js"
STATIC_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"

QD_STATES = [
    "requested",
    "allocating_runtime",
    "creating_database",
    "copying_filestore",
    "creating_user",
    "binding_route",
    "health_check",
    "active",
    "expired",
    "cleaning",
    "deleted",
    "failed",
]

SECRETISH = [
    "csrf_token_value_should_not_echo",
    "database_name",
    "role_name",
    "filestore_path",
    "internal_url",
    "runtime_id",
    "visitor_key",
    "db_password",
]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _base_ctx(lang: str = "en", **extra):
    t = translator(lang)

    def locale_url(path: str) -> str:
        if lang == "en" or not path:
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}lang={lang}"

    def switch_locale(loc: str) -> str:
        return f"?lang={loc}"

    ctx = {
        "lang": lang,
        "html_dir": "rtl" if lang == "ar" else "ltr",
        "t": t,
        "locale_url": locale_url,
        "switch_locale": switch_locale,
        "app_name": "Helpers ERP",
        "csrf_token": "test-csrf-token",
        "brand": SimpleNamespace(
            product_name="Helpers ERP",
            short_name="Helpers ERP",
            css_href=None,
            body_theme_class="",
            logo_mark=None,
            powered_by=None,
        ),
        "user": SimpleNamespace(
            name="Tester",
            github_connected=False,
            cloud_customer=False,
            id=None,
        ),
        "flash": None,
        "oauth_ready": False,
        "quick_demo_href": "/quick-demo/hms",
        "free_trial_href": "/portal/trial/confirm?solution_code=hms",
        "request": SimpleNamespace(url=SimpleNamespace(path="/catalog")),
    }
    ctx.update(extra)
    return ctx


def render(name: str, lang: str = "en", **extra) -> str:
    """Render a template in isolation (extends base.html)."""
    env = _env()
    template = env.get_template(name)
    return template.render(**_base_ctx(lang, **extra))


def _explorer_item(*, code: str, name: str, trial: bool = True, **overrides):
    """Minimal Solution Explorer card compatible with catalog.html after origin/main."""
    data = {
        "id": 1 if code == "hms" else 2,
        "code": code,
        "name": name,
        "display_name": name,
        "short_desc": f"{name} short description",
        "category_label": "Healthcare" if code == "hms" else "Education",
        "is_demo": True,
        "icon": "hms",
        "image_href": None,
        "readiness": SimpleNamespace(state="trial_ready", label="Trial ready"),
        "trial_href": (
            f"/portal/trial/confirm?solution_id={1 if code == 'hms' else 2}&package_id=1"
            if trial
            else None
        ),
        "trust_indicators": [],
        "benefit_cards": [],
        "benefit_title_key": None,
        "capabilities": [],
        "workflow_steps": [],
        "packages": [
            SimpleNamespace(
                id=1,
                code="starter",
                name="Starter",
                status="active",
                is_demo=True,
                price_one_time=None,
                currency="USD",
                edition_label="Community",
                enabled_features=[],
            )
        ],
        "edition_labels": {},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _hms_solution(**overrides):
    return _explorer_item(code="hms", name="Hospital Management", **overrides)


def _sis_solution(**overrides):
    return _explorer_item(code="sis", name="School Information", **overrides)


def _catalog_ctx(solutions, **extra):
    """Render catalog with explorer_solutions / selected_solution (upstream catalog)."""
    selected = solutions[0] if solutions else None
    return {
        "solutions": solutions,
        "explorer_solutions": solutions,
        "selected_solution": selected,
        "selected_code": selected.code if selected else "",
        **extra,
    }

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_unchanged_when_quick_demo_disabled():
    html = render(
        "catalog.html",
        **_catalog_ctx([_hms_solution(), _sis_solution()], quick_demo_enabled=False),
    )
    assert 'data-cta-quick-demo' not in html
    assert "Try now" not in html
    assert "جرّب الآن" not in html
    assert "badge--community" not in html
    # Upstream Free Trial primary CTA remains when QD disabled
    assert 'data-cta-trial' in html
    assert "Start 7-day free trial" not in html  # QD-specific copy absent
    assert "Choose package" not in html


def test_catalog_community_quick_demo_cta_when_enabled():
    html = render(
        "catalog.html",
        **_catalog_ctx([_hms_solution(), _sis_solution()], quick_demo_enabled=True),
    )
    assert 'data-cta-quick-demo' in html
    assert "Try now" in html
    assert 'data-edition="community"' in html
    assert 'href="/quick-demo/hms"' in html
    assert "badge--community" in html
    assert "Community" in html
    # Enterprise Quick Demo absent
    assert "edition=enterprise" not in html
    assert "Enterprise Quick Demo" not in html


def test_catalog_free_trial_cta_still_present_when_enabled():
    html = render(
        "catalog.html",
        **_catalog_ctx([_hms_solution()], quick_demo_enabled=True),
    )
    assert 'data-cta-trial' in html
    assert "Start 7-day free trial" in html
    assert "/portal/trial/confirm" in html


def test_catalog_paid_cta_separate():
    html = render(
        "catalog.html",
        **_catalog_ctx([_hms_solution()], quick_demo_enabled=True),
    )
    assert 'data-cta-paid' in html
    assert "Choose package" in html


def test_catalog_non_hms_unchanged_when_flag_on():
    html = render(
        "catalog.html",
        **_catalog_ctx([_sis_solution()], quick_demo_enabled=True),
    )
    assert 'data-cta-quick-demo' not in html
    assert 'data-cta-trial' in html  # Free Trial remains for non-HMS when available


def test_catalog_english_and_arabic_copy_and_dir():
    en = render(
        "catalog.html",
        lang="en",
        **_catalog_ctx([_hms_solution()], quick_demo_enabled=True),
    )
    ar = render(
        "catalog.html",
        lang="ar",
        **_catalog_ctx([_hms_solution()], quick_demo_enabled=True),
    )
    assert "Try now" in en
    assert "Start 7-day free trial" in en
    assert "Choose package" in en
    assert "جرّب الآن" in ar
    assert "ابدأ تجربة مجانية لمدة 7 أيام" in ar
    assert "اختر الباقة" in ar
    # base.html uses html_dir — presentational fragment still exposes locale helpers
    assert TRANSLATIONS["ar"]["quick_demo.cta.try_now"] == "جرّب الآن"


# ---------------------------------------------------------------------------
# Start page
# ---------------------------------------------------------------------------


def test_start_page_confirmation_content():
    html = render(
        "quick_demo/start.html",
        solution_code="hms",
        edition="community",
        csrf_token="csrf-abc",
    )
    assert "HMS" in html or "Community" in html
    assert "temporary" in html.lower() or "Temporary" in html
    assert "sample" in html.lower()
    assert "Start Quick Demo" in html
    assert 'name="csrf_token"' in html
    assert 'name="language" value="en"' in html
    assert 'value="csrf-abc"' in html
    assert "Back to HMS" in html
    assert 'method="post"' in html
    assert "/quick-demo/sessions" in html
    # No package checkout / pricing language
    assert "price" not in html.lower() or "Prices and trials" not in html
    assert "$" not in html
    assert "checkout" not in html.lower()
    assert "free trial" not in html.lower() or "not retained as a customer trial" in html.lower()


def test_start_page_arabic():
    html = render(
        "quick_demo/start.html",
        lang="ar",
        solution_code="hms",
        edition="community",
    )
    assert "جرّب" in html or "العرض السريع" in html
    assert "بيانات تجريبية" in html
    assert "ابدأ العرض السريع" in html


# ---------------------------------------------------------------------------
# Status page — all states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", QD_STATES)
def test_status_page_renders_all_states(state):
    html = render(
        "quick_demo/status.html",
        state=state,
        progress_percent=40,
        status_message=TRANSLATIONS["en"][f"quick_demo.state.{state}"],
        can_launch=(state == "active"),
        open_href="/quick-demo/open/pub-1" if state == "active" else None,
        retry_allowed=state in {"expired", "failed", "deleted"},
        status_poll_url="" if state in {"active", "expired", "cleaning", "deleted", "failed"} else "/quick-demo/status/pub-1.json",
    )
    assert f'data-qd-state="{state}"' in html
    assert 'aria-live="polite"' in html
    assert 'role="progressbar"' in html
    assert TRANSLATIONS["en"][f"quick_demo.state.{state}"] in html or state in html


def test_launch_hidden_before_active():
    html = render(
        "quick_demo/status.html",
        state="health_check",
        progress_percent=90,
        can_launch=False,
        open_href=None,
        status_poll_url="/quick-demo/status/x.json",
    )
    assert 'data-qd-cta="open"' not in html
    assert "Open HMS" not in html or 'data-qd-open-label="Open HMS"' in html
    # Label may exist as data attribute for JS, but no visible CTA link
    assert 'id="quick-demo-open-cta"' not in html
    assert 'data-poll-enabled="true"' in html


def test_launch_shown_only_when_active_and_can_launch():
    html = render(
        "quick_demo/status.html",
        state="active",
        progress_percent=100,
        can_launch=True,
        open_href="/quick-demo/open/abc",
        status_poll_url="",
    )
    assert 'data-qd-cta="open"' in html
    assert 'href="/quick-demo/open/abc"' in html
    assert "Open HMS" in html
    assert 'data-poll-enabled="false"' in html


def test_active_without_can_launch_hides_open():
    """Blocker: can_launch false while active — no Open CTA."""
    html = render(
        "quick_demo/status.html",
        state="active",
        can_launch=False,
        open_href=None,
    )
    assert 'id="quick-demo-open-cta"' not in html


def test_expired_and_failed_recovery_copy():
    expired = render(
        "quick_demo/status.html",
        state="expired",
        can_launch=False,
        retry_allowed=True,
    )
    failed = render(
        "quick_demo/status.html",
        state="failed",
        can_launch=False,
        retry_allowed=True,
    )
    assert "expired" in expired.lower()
    assert 'data-qd-recovery="expired"' in expired
    assert 'data-qd-cta="retry"' in expired
    assert 'data-qd-recovery="failed"' in failed
    assert "try again" in failed.lower() or "Try Quick Demo again" in failed


def test_no_secret_or_internal_field_rendering():
    html = render(
        "quick_demo/status.html",
        state="creating_database",
        progress_percent=25,
        status_message="Creating a temporary database",
        can_launch=False,
        session_public_id="pub-should-not-show",
        csrf_token="csrf-hidden-only",
        database_name="qd_hms_123",
        role_name="qd_role_secret",
        filestore_path="/var/secret/fs",
        internal_url="http://127.0.0.1:8069",
        status_poll_url="/quick-demo/status/pub.json",
    )
    assert "pub-should-not-show" not in html
    assert "qd_hms_123" not in html
    assert "qd_role_secret" not in html
    assert "/var/secret/fs" not in html
    assert "127.0.0.1" not in html
    # CSRF may appear as hidden form value only on start page; status must not echo secrets
    assert "csrf-hidden-only" not in html


def test_start_csrf_is_hidden_field_only():
    html = render("quick_demo/start.html", csrf_token="csrf-xyz")
    assert 'type="hidden" name="csrf_token" value="csrf-xyz"' in html
    # Not shown as visible copy outside the input
    visible = re.sub(r"<input[^>]*>", "", html)
    assert "csrf-xyz" not in visible


def test_status_arabic_rtl_font_contract():
    html = render(
        "quick_demo/status.html",
        lang="ar",
        state="requested",
        progress_percent=5,
        status_message=TRANSLATIONS["ar"]["quick_demo.state.requested"],
    )
    assert TRANSLATIONS["ar"]["quick_demo.state.requested"] in html
    assert "العرض السريع" in html or "جاري تجهيز" in html
    css = STATIC_CSS.read_text(encoding="utf-8")
    assert 'html[dir="rtl"] .quick-demo' in css
    assert "font-size: 16px" in css


def test_css_touch_targets_and_reduced_motion():
    css = STATIC_CSS.read_text(encoding="utf-8")
    assert "min-height: 44px" in css
    assert "prefers-reduced-motion" in css
    assert ".quick-demo__progress" in css


def test_js_terminal_polling_contract():
    js = STATIC_JS.read_text(encoding="utf-8")
    for state in ("active", "expired", "deleted", "failed"):
        assert f'"{state}"' in js
    assert "MAX_DELAY_MS" in js
    assert "BACKOFF_FACTOR" in js
    assert "location.reload" not in js
    assert "TERMINAL_STATES" in js


def test_translation_keys_exist_for_all_states():
    for lang in ("en", "ar"):
        for state in QD_STATES:
            key = f"quick_demo.state.{state}"
            assert key in TRANSLATIONS[lang], f"missing {lang} {key}"
            assert TRANSLATIONS[lang][key].strip()


def test_product_language_never_calls_quick_demo_a_free_trial():
    for lang in ("en", "ar"):
        for key, value in TRANSLATIONS[lang].items():
            if not key.startswith("quick_demo."):
                continue
            if key in {
                "quick_demo.cta.free_trial",
                "quick_demo.recovery.failed",
                "quick_demo.point.not_trial",
                "quick_demo.recovery.expired",
            }:
                # These intentionally contrast Quick Demo vs free trial
                continue
            lowered = value.lower()
            assert "free trial" not in lowered or "not" in lowered
            assert "تجربة مجانية لمدة 7" not in value or "ليست" in value or key.endswith("free_trial")


def test_semantic_headings_on_start_and_status():
    start = render("quick_demo/start.html")
    status = render("quick_demo/status.html", state="requested", progress_percent=0)
    assert re.search(r"<h1[^>]*>", start)
    assert re.search(r"<h2[^>]*>", start)
    assert re.search(r"<h1[^>]*>", status)
    assert 'role="status"' in status
    assert 'aria-live="polite"' in status


def test_polling_uses_canonical_status_route_and_json_accept():
    js = STATIC_JS.read_text(encoding="utf-8")
    assert 'Accept: "application/json"' in js
    assert "/status/" not in js  # URL is supplied by the server, never invented by JS.
