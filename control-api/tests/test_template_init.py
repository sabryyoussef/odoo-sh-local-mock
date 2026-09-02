"""Template identifier generation tests."""

from app.models import Solution, TemplateDatabase
from app.services.catalog_service import create_solution
from app.schemas_saas import SolutionCreate
from app.services.template_init_service import template_postgres_name


def test_template_postgres_name_version_starts_with_letter(db):
    sol = create_solution(
        db,
        SolutionCreate(name="Vet", code="vet-hospital-tpl", description="", is_demo=True),
    )
    tpl = TemplateDatabase(
        solution_id=sol.id,
        name="tpl",
        solution_version="1.0.0-demo",
        odoo_version="19.0",
        state="draft",
    )
    name = template_postgres_name(tpl, sol)
    assert name.startswith("mosh_tpl_vet_hospital_tpl_v")
