"""Presentation helpers for the Ready Solutions catalog explorer.

Builds view models from existing Solution / Package / DeploymentProfile /
Artifact domain data. Does not mutate provisioning, Proxmox, or HC3.x paths.
Documentation is metadata-only — unpublished sections render an honest
"coming soon" state without fabricated URLs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.i18n import has_translation, translate
from app.models import Package, Solution, User
from app.services.portal_service import demo_resume_path
from app.services.ready_solution_profile_service import list_active_profiles_for_solution
from app.services.saas_serialization import (
    artifact_to_dict,
    csv_to_features,
    csv_to_modules,
    deployment_profile_to_dict,
    package_to_dict,
)

# Stable category fallbacks when Solution.category is unset.
_CATEGORY_BY_CODE: dict[str, str] = {
    "hms": "healthcare",
    "sis": "education",
    "vet-hospital": "veterinary",
    "generic": "general",
}

_ICON_BY_CODE: dict[str, str] = {
    "hms": "hospital",
    "sis": "school",
    "vet-hospital": "paw",
    "generic": "layers",
}

# Public-facing module names — presentation-layer only. Internal tech names preserved.
_MODULE_PUBLIC_NAMES: dict[str, dict[str, str]] = {
    "acs_hms_base": {"en": "Hospital Core", "ar": "النواة الطبية"},
    "acs_hms": {"en": "Clinical Operations", "ar": "العمليات السريرية"},
    "alzaeem_acs_hms_fix": {"en": "Odoo 19 Compatibility Layer", "ar": "طبقة التوافق مع Odoo 19"},
    "openeducat_core": {"en": "Education Core", "ar": "نواة التعليم"},
    "openeducat_admission": {"en": "Admissions Management", "ar": "إدارة القبول"},
    "openeducat_fees": {"en": "Fee Management", "ar": "إدارة الرسوم"},
    "openeducat_exam": {"en": "Examination Management", "ar": "إدارة الامتحانات"},
    "openeducat_parent": {"en": "Parent Portal", "ar": "بوابة أولياء الأمور"},
    "openeducat_classroom": {"en": "Classroom Management", "ar": "إدارة الفصول"},
    "openeducat_timetable": {"en": "Timetable Management", "ar": "إدارة الجداول"},
    "openeducat_attendance": {"en": "Attendance Management", "ar": "إدارة الحضور"},
    "openeducat_library": {"en": "Library Management", "ar": "إدارة المكتبة"},
    "openeducat_assignment": {"en": "Assignment Management", "ar": "إدارة الواجبات"},
    "openeducat_erp": {"en": "Education ERP", "ar": "ERP التعليم"},
}

# Modules that are NOT advertised publicly (vendor branding in manifest etc.)

# HMS-specific verified capabilities mapping (clinic/base only — no hospitalization)
_HMS_CAPABILITIES: list[dict[str, Any]] = [
    {"code": "patients", "icon": "👤", "benefit_ar": "سجلات مرضى مركزية وسهل الوصول", "benefit_en": "Centralized, accessible patient records"},
    {"code": "physicians", "icon": "🩺", "benefit_ar": "جداول وأعباء أطباء واضحة", "benefit_en": "Clear physician schedules and workload"},
    {"code": "appointments", "icon": "📅", "benefit_ar": "حجز وتتبع المواعيد والكشوفات", "benefit_en": "Book and track appointments and consultations"},
    {"code": "prescriptions", "icon": "💊", "benefit_ar": "أوامر طبية وإدارة الأدوية", "benefit_en": "Medical orders and medication management"},
    {"code": "procedures", "icon": "⚕️", "benefit_ar": "خدمات وإجراءات قابلة للفوترة", "benefit_en": "Billable services and procedures"},
    {"code": "billing", "icon": "🧾", "benefit_ar": "فوترة مالية وتقارير واضحة", "benefit_en": "Financial billing and clear reporting"},
]

# Audience cards for HMS (verified for clinic/base — no hospitalization/wards)
_HMS_AUDIENCE: list[dict[str, Any]] = [
    {
        "code": "clinics",
        "icon_ar": "🏥", "icon_en": "🏥",
        "title_key": "catalog.hms.who.clinics",
        "desc_key": "catalog.hms.who.clinics.desc",
    },
    {
        "code": "centers",
        "icon_ar": "🏨", "icon_en": "🏨",
        "title_key": "catalog.hms.who.centers",
        "desc_key": "catalog.hms.who.centers.desc",
    },
    {
        "code": "staff",
        "icon_ar": "👥", "icon_en": "👥",
        "title_key": "catalog.hms.who.staff",
        "desc_key": "catalog.hms.who.staff.desc",
    },
]

# Documentation cards
_HMS_DOCS: list[dict[str, Any]] = [
    {
        "id": "start",
        "title_key": "catalog.docs.start.title",
        "summary_key": "catalog.docs.start.summary",
        "icon": "🚀",
        "status": "published",
    },
    {
        "id": "workflow",
        "title_key": "catalog.docs.workflow.title",
        "summary_key": "catalog.docs.workflow.summary",
        "icon": "🔄",
        "status": "published",
    },
    {
        "id": "admin",
        "title_key": "catalog.docs.admin.title",
        "summary_key": "catalog.docs.admin.summary",
        "icon": "🛡️",
        "status": "published",
    },
]


# Service-benefit cards for HMS
_HMS_BENEFIT_CARDS: list[dict[str, Any]] = [
    {
        "code": "safety",
        "icon": "🛡️",
        "title_key": "catalog.benefit.1.title",
        "desc_key": "catalog.benefit.1.desc",
    },
    {
        "code": "billing",
        "icon": "💰",
        "title_key": "catalog.benefit.2.title",
        "desc_key": "catalog.benefit.2.desc",
    },
    {
        "code": "scheduling",
        "icon": "📅",
        "title_key": "catalog.benefit.3.title",
        "desc_key": "catalog.benefit.3.desc",
    },
    {
        "code": "secure",
        "icon": "🔒",
        "title_key": "catalog.benefit.4.title",
        "desc_key": "catalog.benefit.4.desc",
    },
]

# Education-specific verified capabilities mapping (only for SIS modules)
_SIS_CAPABILITIES: list[dict[str, Any]] = [
    {"code": "students", "icon": "🎓", "benefit_ar": "سجلات طلاب مركزية شاملة", "benefit_en": "Centralized, comprehensive student records"},
    {"code": "admissions", "icon": "📝", "benefit_ar": "قبول وتسجيل منظّم للطلاب الجدد", "benefit_en": "Organized admission and enrollment"},
    {"code": "courses", "icon": "📚", "benefit_ar": "إدارة المقررات والفصول الدراسية", "benefit_en": "Manage courses and class sections"},
    {"code": "attendance", "icon": "✅", "benefit_ar": "تتبع الحضور والغياب بسهولة", "benefit_en": "Track attendance and absences easily"},
    {"code": "fees", "icon": "💰", "benefit_ar": "الرسوم وخطط السداد والتحصيل", "benefit_en": "Fee plans, payment tracking, collection"},
    {"code": "exams", "icon": "📋", "benefit_ar": "الامتحانات والنتائج ودرجات التقييم", "benefit_en": "Examinations, results, and grading"},
    {"code": "timetable", "icon": "🗓️", "benefit_ar": "جداول دراسية منظمة", "benefit_en": "Organized academic timetables"},
    {"code": "parent_portal", "icon": "👨‍👩‍👧", "benefit_ar": "بوابة منظمة للطلاب وأولياء الأمور", "benefit_en": "Organized portal for students and parents"},
]

# Audience cards for SIS (education-focused)
_SIS_AUDIENCE: list[dict[str, Any]] = [
    {
        "code": "private-schools",
        "icon_ar": "🏫", "icon_en": "🏫",
        "title_key": "catalog.sis.who.private_schools",
        "desc_key": "catalog.sis.who.private_schools.desc",
    },
    {
        "code": "nurseries",
        "icon_ar": "🧸", "icon_en": "🧸",
        "title_key": "catalog.sis.who.nurseries",
        "desc_key": "catalog.sis.who.nurseries.desc",
    },
    {
        "code": "institutes",
        "icon_ar": "🎓", "icon_en": "🎓",
        "title_key": "catalog.sis.who.institutes",
        "desc_key": "catalog.sis.who.institutes.desc",
    },
    {
        "code": "colleges",
        "icon_ar": "🏛️", "icon_en": "🏛️",
        "title_key": "catalog.sis.who.colleges",
        "desc_key": "catalog.sis.who.colleges.desc",
    },
    {
        "code": "multi-branch",
        "icon_ar": "🏢", "icon_en": "🏢",
        "title_key": "catalog.sis.who.multi_branch",
        "desc_key": "catalog.sis.who.multi_branch.desc",
    },
]

# Documentation cards for SIS
_SIS_DOCS: list[dict[str, Any]] = [
    {"id": "start", "title_key": "catalog.docs.sis.start.title", "summary_key": "catalog.docs.sis.start.summary", "icon": "🚀", "status": "published"},
    {"id": "academic-year", "title_key": "catalog.docs.sis.academic_year.title", "summary_key": "catalog.docs.sis.academic_year.summary", "icon": "📅", "status": "published"},
    {"id": "students", "title_key": "catalog.docs.sis.students.title", "summary_key": "catalog.docs.sis.students.summary", "icon": "🎓", "status": "published"},
    {"id": "fees", "title_key": "catalog.docs.sis.fees.title", "summary_key": "catalog.docs.sis.fees.summary", "icon": "💰", "status": "published"},
    {"id": "attendance", "title_key": "catalog.docs.sis.attendance.title", "summary_key": "catalog.docs.sis.attendance.summary", "icon": "✅", "status": "published"},
    {"id": "exams", "title_key": "catalog.docs.sis.exams.title", "summary_key": "catalog.docs.sis.exams.summary", "icon": "📋", "status": "published"},
    {"id": "portal", "title_key": "catalog.docs.sis.portal.title", "summary_key": "catalog.docs.sis.portal.summary", "icon": "👨‍👩‍👧", "status": "published"},
    {"id": "users", "title_key": "catalog.docs.sis.users.title", "summary_key": "catalog.docs.sis.users.summary", "icon": "🛡️", "status": "published"},
]

# Service-benefit cards for SIS
_SIS_BENEFIT_CARDS: list[dict[str, Any]] = [
    {"code": "unified-record", "icon": "🎓", "title_key": "catalog.sis.benefit.1.title", "desc_key": "catalog.sis.benefit.1.desc"},
    {"code": "academic-efficiency", "icon": "📚", "title_key": "catalog.sis.benefit.2.title", "desc_key": "catalog.sis.benefit.2.desc"},
    {"code": "family-communication", "icon": "👨‍👩‍👧", "title_key": "catalog.sis.benefit.3.title", "desc_key": "catalog.sis.benefit.3.desc"},
    {"code": "data-driven", "icon": "📊", "title_key": "catalog.sis.benefit.4.title", "desc_key": "catalog.sis.benefit.4.desc"},
]

# Education workflow steps
_SIS_WORKFLOW_STEPS: list[dict[str, str]] = [
    {"id": "inquiry", "label_key": "catalog.sis.workflow.step1"},
    {"id": "review", "label_key": "catalog.sis.workflow.step2"},
    {"id": "enrollment", "label_key": "catalog.sis.workflow.step3"},
    {"id": "schedule", "label_key": "catalog.sis.workflow.step4"},
    {"id": "fees", "label_key": "catalog.sis.workflow.step5"},
    {"id": "exams", "label_key": "catalog.sis.workflow.step6"},
    {"id": "followup", "label_key": "catalog.sis.workflow.step7"},
]

# Dispatch table: maps solution code to its presentation data.
# Prevents cross-industry content leakage. Missing code → empty safe fallback.
_PRESENTATION_BY_CODE: dict[str, dict[str, Any]] = {
    "hms": {
        "benefit_title_key": "catalog.benefit.card.title",
        "capabilities": _HMS_CAPABILITIES,
        "audience_cards": _HMS_AUDIENCE,
        "docs_full": _HMS_DOCS,
        "benefit_cards": _HMS_BENEFIT_CARDS,
        "workflow_steps": [
            {"id": "registration", "label_key": "catalog.workflow.step1"},
            {"id": "consultation", "label_key": "catalog.workflow.step2"},
            {"id": "services", "label_key": "catalog.workflow.step3"},
            {"id": "invoice", "label_key": "catalog.workflow.step4"},
            {"id": "payment", "label_key": "catalog.workflow.step5"},
        ],
    },
    "sis": {
        "benefit_title_key": "catalog.sis.benefit.title",
        "capabilities": _SIS_CAPABILITIES,
        "audience_cards": _SIS_AUDIENCE,
        "docs_full": _SIS_DOCS,
        "benefit_cards": _SIS_BENEFIT_CARDS,
        "workflow_steps": _SIS_WORKFLOW_STEPS,
    },
}


def _presentation_data_for(solution_code: str) -> dict[str, Any]:
    """Return solution-specific presentation data.

    Never let one solution inherit another's content.
    Missing data returns empty lists (safe fallback).
    """
    return _PRESENTATION_BY_CODE.get(solution_code, {
        "capabilities": [],
        "audience_cards": [],
        "docs_full": [],
        "benefit_cards": [],
        "workflow_steps": [],
    })

_VENDOR_MODULE_NAMES = {
    "Base - Hospital Management System ( HMS by AlmightyCS )",
    "Clinic - Hospital Management System ( HMS by AlmightyCS )",
    "Edafa Core",
    "Edafa Admission",
    "Edafa Fees",
    "Edafa Exam",
    "Edafa Parent",
    "Edafa Classroom",
    "Edafa Timetable",
    "Edafa Attendance",
    "Edafa Library",
    "Edafa Assignment",
    "Edafa ERP",
}

# Audience / coverage copy keys — only used when the translation catalog has them.
# Never invent product claims beyond stored description + package features.
_DOC_SECTION_KEYS: tuple[str, ...] = (
    "user_guide",
    "installation_guide",
    "developer_guide",
    "faq",
    "videos",
)


def _t(locale: str, key: str, fallback: str = "") -> str:
    if has_translation(key):
        return translate(locale, key)
    return fallback or translate(locale, key)


def _category_code(solution: Solution) -> str:
    raw = (solution.category or solution.industry_code or "").strip().lower()
    if raw:
        return raw
    return _CATEGORY_BY_CODE.get(solution.code, "general")


def _active_packages(solution: Solution) -> list[Package]:
    return [p for p in (solution.packages or []) if p.status == "active"]


def _demo_package(solution: Solution) -> Package | None:
    packages = _active_packages(solution)
    for pkg in packages:
        if pkg.is_demo:
            return pkg
    return packages[0] if packages else None


def _feature_labels(locale: str, features: list[str]) -> list[str]:
    labels: list[str] = []
    for feat in features:
        code = (feat or "").strip()
        if not code:
            continue
        key = f"catalog.feature.{code}"
        if has_translation(key):
            labels.append(translate(locale, key))
        else:
            labels.append(code.replace("_", " ").strip().title())
    return labels


def _module_documentation(locale: str, solution_code: str) -> dict[str, Any]:
    """Build explorer documentation from real Odoo module metadata."""
    from app.services.solution_module_docs_service import get_solution_module_docs

    raw = get_solution_module_docs(solution_code)
    modules = []
    for mod in raw.get("modules") or []:
        if not mod.get("available"):
            continue
        tech_name = mod.get("technical_name", "")
        raw_name = mod.get("name") or tech_name
        # Use public-facing name if available; never expose vendor manifest names
        public_names = _MODULE_PUBLIC_NAMES.get(tech_name, {})
        display_name = public_names.get(locale) or public_names.get("en") or raw_name
        if raw_name in _VENDOR_MODULE_NAMES:
            display_name = public_names.get(locale) or public_names.get("en") or tech_name
        modules.append(
            {
                "technical_name": tech_name,
                "name": display_name,
                "public_name_en": public_names.get("en", ""),
                "public_name_ar": public_names.get("ar", ""),
                "purpose": mod.get("purpose") or "",
                "depends_count": mod.get("depends_count") or 0,
                "depends": mod.get("depends") or [],
                "menus": mod.get("menus") or [],
                "version": mod.get("version") or "",
                "category": mod.get("category") or "",
                "icon_href": mod.get("icon_href"),
                "is_technical": True,
            }
        )

    # Keep classic guide keys for nav compatibility; mark derived when modules exist.
    classic_blocks = []
    for section_id in _DOC_SECTION_KEYS:
        if modules and section_id in {"user_guide", "developer_guide"}:
            classic_blocks.append(
                {
                    "id": section_id,
                    "nav_id": f"docs-{section_id}",
                    "title": translate(locale, f"catalog.docs.{section_id}"),
                    "status": "derived",
                    "url": None,
                    "summary": translate(locale, "catalog.docs.derived_from_modules"),
                }
            )
        else:
            classic_blocks.append(
                {
                    "id": section_id,
                    "nav_id": f"docs-{section_id}",
                    "title": translate(locale, f"catalog.docs.{section_id}"),
                    "status": "unpublished",
                    "url": None,
                    "summary": translate(locale, "catalog.docs.coming_soon"),
                }
            )

    return {
        "image_href": raw.get("image_href"),
        "modules": modules,
        "workflows": raw.get("workflows") or [],
        "available_count": raw.get("available_count") or 0,
        "built_from": raw.get("built_from") or "empty",
        "classic_blocks": classic_blocks,
        "has_module_docs": bool(modules),
    }


def _readiness(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Truthful readiness mapping:
    - deployment_ready + verified → production_ready
    - verified only → trial_ready (not deployment-ready)
    - neither → not_verified (never show "جاهز للنشر")
    """
    if not artifacts:
        return {
            "state": "unknown",
            "label_key": "catalog.readiness.unknown",
            "verified": False,
            "deployment_ready": False,
        }
    verified = any(a.get("is_verified") for a in artifacts)
    ready = any(a.get("deployment_ready") for a in artifacts)
    if verified and ready:
        state, key = "production_ready", "catalog.readiness.production_ready"
    elif verified:
        state, key = "trial_ready", "catalog.readiness.trial_ready"
    else:
        state, key = "not_verified", "catalog.readiness.not_verified"
    return {
        "state": state,
        "label_key": key,
        "verified": verified,
        "deployment_ready": ready,
    }


def _trial_urls(
    *,
    locale: str,
    solution: Solution,
    demo_pkg: Package | None,
    user: User | None,
    user_view: dict[str, Any],
    db: Session,
) -> dict[str, Any]:
    """Reuse the existing portal trial confirm path — no parallel demo flow.

    ``/portal/trial/confirm`` already redirects unauthenticated users to login
    and resumes existing demos via ``demo_resume_path``.
    """
    del locale  # login next-path inside portal preserves session locale cookie
    existing = None
    if user is not None:
        existing = demo_resume_path(db, user.id, solution.id)

    if existing:
        return {
            "trial_href": existing,
            "trial_label_key": "catalog.cta_open_demo",
            "live_demo_href": existing,
            "needs_signin": False,
        }

    if not demo_pkg or not solution.is_demo:
        return {
            "trial_href": None,
            "trial_label_key": "catalog.contact_sales",
            "live_demo_href": None,
            "needs_signin": False,
        }

    confirm = f"/portal/trial/confirm?{urlencode({'solution_id': solution.id, 'package_id': demo_pkg.id})}"
    signed_in = bool(
        user_view.get("github_connected") or user_view.get("cloud_customer")
    )
    # Always point at the portal trial gate — it handles auth + eligibility.
    return {
        "trial_href": confirm,
        "trial_label_key": "catalog.cta_start_trial",
        "live_demo_href": None,
        "needs_signin": not signed_in,
    }



# Edition codes per package
_EDITION_BY_PACKAGE: dict[str, dict[str, str]] = {
    "clinic-essentials": {"en": "Community / Enterprise", "ar": "Community / Enterprise"},
    "healthcare-premium": {"en": "Community / Enterprise", "ar": "Community / Enterprise"},
    "complete-community": {"en": "Community Edition", "ar": "Community Edition"},
    "complete-enterprise": {"en": "Enterprise Edition", "ar": "Enterprise Edition"},
    "education-starter": {"en": "Community Edition", "ar": "Community Edition"},
    "complete-school": {"en": "Community Edition", "ar": "Community Edition"},
    "education-enterprise": {"en": "Community Edition", "ar": "Community Edition"},
}


def _package_with_edition(pkg: Package, locale: str) -> dict[str, Any]:
    data = package_to_dict(pkg)
    ed = _EDITION_BY_PACKAGE.get(pkg.code, {})
    data["edition_label"] = ed.get(locale) or ed.get("en") or ""
    return data


def build_solution_explorer_item(
    db: Session,
    solution: Solution,
    *,
    locale: str,
    user: User | None,
    user_view: dict[str, Any],
) -> dict[str, Any]:
    data = _presentation_data_for(solution.code)

    packages = [_package_with_edition(p, locale) for p in _active_packages(solution)]
    demo_pkg = _demo_package(solution)
    profiles_orm = list_active_profiles_for_solution(db, solution.id)
    profiles = [deployment_profile_to_dict(p) for p in profiles_orm]
    artifacts = [artifact_to_dict(a) for a in (solution.artifacts or [])]
    readiness = _readiness(artifacts)

    feature_codes: list[str] = []
    for pkg in _active_packages(solution):
        for feat in csv_to_features(pkg.enabled_features):
            if feat not in feature_codes:
                feature_codes.append(feat)

    required_modules = csv_to_modules(solution.required_modules)
    optional_modules = csv_to_modules(solution.optional_modules)

    # Prefer package modules for "main functions" when present.
    function_modules: list[str] = []
    if demo_pkg and demo_pkg.enabled_modules:
        function_modules = csv_to_modules(demo_pkg.enabled_modules)
    elif required_modules:
        function_modules = list(required_modules)

    category_code = _category_code(solution)
    name_key = f"catalog.solution.{solution.code}.name"
    subtitle_key = f"catalog.solution.{solution.code}.subtitle"
    short_key = f"catalog.solution.{solution.code}.short"
    audience_key = f"catalog.solution.{solution.code}.audience"

    display_name = _t(locale, name_key, solution.name)
    subtitle = _t(locale, subtitle_key, "") if has_translation(subtitle_key) else ""
    short_desc = (
        _t(locale, short_key, "")
        if has_translation(short_key)
        else (solution.description or "").strip()
    )
    audience = _t(locale, audience_key, "") if has_translation(audience_key) else ""

    docs = _module_documentation(locale, solution.code)
    trial = _trial_urls(
        locale=locale,
        solution=solution,
        demo_pkg=demo_pkg,
        user=user,
        user_view=user_view,
        db=db,
    )

    # System requirements: prefer default/demo profile numbers when available.
    req_profile = None
    for prof in profiles:
        if prof.get("is_default") or prof.get("code") == "demo":
            req_profile = prof
            break
    if req_profile is None and profiles:
        req_profile = profiles[0]

    # Prefer module-derived feature chips when package features are empty.
    display_features = _feature_labels(locale, feature_codes)
    if not display_features and docs.get("modules"):
        display_features = [m["name"] for m in docs["modules"][:8]]

    nav_sections: list[dict[str, Any]] = [
        {"id": "overview", "label": translate(locale, "catalog.nav.overview"), "available": True},
        {
            "id": "features",
            "label": translate(locale, "catalog.nav.features"),
            "available": bool(display_features),
        },
        {
            "id": "functions",
            "label": translate(locale, "catalog.nav.functions"),
            "available": bool(function_modules) or bool(docs.get("workflows")),
        },
        {
            "id": "modules",
            "label": translate(locale, "catalog.nav.modules"),
            "available": bool(docs.get("has_module_docs")),
        },
        {
            "id": "profiles",
            "label": translate(locale, "catalog.nav.profiles"),
            "available": bool(profiles) or bool(packages),
        },
        {
            "id": "requirements",
            "label": translate(locale, "catalog.nav.requirements"),
            "available": bool(req_profile) or bool(required_modules),
        },
        {
            "id": "docs",
            "label": translate(locale, "catalog.nav.docs"),
            "available": True,
        },
    ]
    nav_sections = [s for s in nav_sections if s["available"]]

    profile_summary = ""
    if profiles:
        names = [p.get("name") for p in profiles if p.get("name")]
        profile_summary = " · ".join(names[:3])
    elif packages:
        names = [p.get("name") for p in packages if p.get("name")]
        profile_summary = " · ".join(names[:3])

    return {
        "id": solution.id,
        "code": solution.code,
        "name": solution.name,
        "display_name": display_name,
        "subtitle": subtitle,
        "description": (solution.description or "").strip(),
        "short_desc": short_desc,
        "audience": audience,
        "odoo_version": solution.odoo_version,
        "current_version": solution.current_version,
        "is_demo": bool(solution.is_demo),
        "category_code": category_code,
        "category_label": translate(locale, f"catalog.category.{category_code}"),
        "icon": _ICON_BY_CODE.get(solution.code, "layers"),
        "image_href": docs.get("image_href"),
        "packages": packages,
        "demo_package": package_to_dict(demo_pkg) if demo_pkg else None,
        "deployment_profiles": profiles,
        "artifacts": artifacts,
        "readiness": {
            **readiness,
            "label": translate(locale, readiness["label_key"]),
        },
        "features": display_features,
        "feature_codes": feature_codes,
        "function_modules": function_modules,
        "required_modules": required_modules,
        "optional_modules": optional_modules,
        "requirements_profile": req_profile,
        "documentation": docs.get("classic_blocks") or [],
        "module_docs": docs,
        "nav_sections": nav_sections,
        "trial_href": trial.get("trial_href"),
        "trial_label": translate(locale, trial.get("trial_label_key") or "catalog.cta_start_trial"),
        "live_demo_href": trial.get("live_demo_href"),
        "needs_signin": bool(trial.get("needs_signin")),
        "profile_count": len(profiles),
        "package_count": len(packages),
        "profile_summary": profile_summary,
        "trust_indicators": [
            {
                "icon": "hosted",
                "label_key": "catalog.trust.hosted",
                "desc_key": "catalog.trust.hosted_desc",
            },
            {
                "icon": "trial",
                "label_key": "catalog.trust.trial",
                "desc_key": "catalog.trust.trial_desc",
            },
            {
                "icon": "support",
                "label_key": "catalog.trust.support",
                "desc_key": "catalog.trust.support_desc",
            },
            {
                "icon": "security",
                "label_key": "catalog.trust.security",
                "desc_key": "catalog.trust.security_desc",
            },
        ],
        "workflow_steps": data["workflow_steps"],
        "capabilities": [
            {
                "code": cap["code"],
                "title": translate(locale, f"catalog.feature.{cap['code']}"),
                "benefit": cap.get(f"benefit_{locale}", cap.get("benefit_en", "")),
                "icon": cap["icon"],
            }
            for cap in data["capabilities"]
        ],
        "audience_cards": [
            {
                "code": aud["code"],
                "title": translate(locale, aud["title_key"]),
                "desc": translate(locale, aud["desc_key"]),
                "icon": aud.get(f"icon_{locale}", aud.get("icon_en", "")),
            }
            for aud in data["audience_cards"]
        ],
        "docs_full": [
            {
                "id": doc["id"],
                "title": translate(locale, doc["title_key"]),
                "summary": translate(locale, doc["summary_key"]),
                "icon": doc["icon"],
                "status": doc["status"],
            }
            for doc in data["docs_full"]
        ],
        "is_clinical_only": solution.code == "hms",
        "benefit_title_key": data.get("benefit_title_key", "catalog.benefit.card.title"),
        "benefit_cards": [
            {
                "code": card["code"],
                "icon": card["icon"],
                "title": translate(locale, card["title_key"]),
                "desc": translate(locale, card["desc_key"]),
            }
            for card in data["benefit_cards"]
        ],
        "comparison_rows": [
            {
                "label_key": "catalog.compare.users",
                "cells": [str(p.get("max_users", "—")) for p in packages],
            },
            {
                "label_key": "catalog.compare.branches",
                "cells": [str(p.get("max_branches", "—")) for p in packages],
            },
            {
                "label_key": "catalog.compare.companies",
                "cells": [str(p.get("max_companies", "—")) for p in packages],
            },
            {
                "label_key": "catalog.compare.storage",
                "cells": [f'{p.get("filestore_quota_mb", 0)} MB' for p in packages],
            },
            {
                "label_key": "catalog.compare.backup",
                "cells": [
                    f'{p.get("backup_frequency_hours", 0)}h / {p.get("backup_retention_days", 0)}d'
                    for p in packages
                ],
            },
            {
                "label_key": "catalog.compare.support",
                "cells": [
                    (p.get("support_sla") or "—").replace("_", " ").title()
                    for p in packages
                ],
            },
            {
                "label_key": "catalog.compare.api",
                "cells": [
                    translate(locale, "common.included") if p.get("api_enabled") else "—"
                    for p in packages
                ],
            },
            {
                "label_key": "catalog.compare.staging",
                "cells": [
                    translate(locale, "common.included") if p.get("staging_enabled") else "—"
                    for p in packages
                ],
            },
            {
                "label_key": "catalog.compare.edition",
                "cells": [
                    p.get("edition_label", translate(locale, "catalog.edition.community"))
                    for p in packages
                ],
            },
        ],
        "edition_labels": {
            "clinic-essentials": translate(locale, "catalog.edition.community"),
            "healthcare-premium": translate(locale, "catalog.edition.community"),
            "complete-community": translate(locale, "catalog.edition.community_only"),
            "complete-enterprise": translate(locale, "catalog.edition.enterprise"),
            "education-starter": translate(locale, "catalog.edition.community"),
            "complete-school": translate(locale, "catalog.edition.community"),
            "education-enterprise": translate(locale, "catalog.edition.community"),
        },
    }


def build_catalog_explorer(
    db: Session,
    solutions: list[Solution],
    *,
    locale: str,
    user: User | None,
    user_view: dict[str, Any],
    selected_code: str | None = None,
) -> dict[str, Any]:
    items = [
        build_solution_explorer_item(db, sol, locale=locale, user=user, user_view=user_view)
        for sol in solutions
    ]
    code = (selected_code or "").strip().lower()
    selected = None
    if code:
        selected = next((item for item in items if item["code"] == code), None)
    if selected is None and items:
        # Prefer HMS as a sensible default when present; else first card.
        selected = next((item for item in items if item["code"] == "hms"), items[0])
    return {
        "solutions": items,
        "selected": selected,
        "selected_code": selected["code"] if selected else None,
    }
