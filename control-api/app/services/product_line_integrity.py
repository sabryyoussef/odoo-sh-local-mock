"""Reject invalid cross-product-line combinations.

Product line is an explicit discriminator and must not be inferred from FKs.
"""

from __future__ import annotations

from app.product_lines import (
    PRODUCT_LINE_DEVELOPER_PLATFORM,
    PRODUCT_LINE_HELPERS_CLOUD,
    PRODUCT_LINE_READY_SOLUTION,
    PRODUCT_LINES,
)


class ProductLineIntegrityError(Exception):
    def __init__(self, message: str, code: str = "product_line_integrity"):
        super().__init__(message)
        self.message = message
        self.code = code


def require_product_line(product_line: str | None) -> str:
    value = (product_line or "").strip()
    if value not in PRODUCT_LINES:
        raise ProductLineIntegrityError(
            f"Unknown product_line={product_line!r}",
            "invalid_product_line",
        )
    return value


def reject_github_on_customer_order(
    *,
    product_line: str,
    github_repository: str | None = None,
    arbitrary_module: str | None = None,
) -> None:
    line = require_product_line(product_line)
    if line in {PRODUCT_LINE_READY_SOLUTION, PRODUCT_LINE_HELPERS_CLOUD}:
        if github_repository:
            raise ProductLineIntegrityError(
                "Ready Solutions and Helpers ERP Cloud orders cannot contain a GitHub repository",
                "github_not_allowed",
            )
        if arbitrary_module:
            raise ProductLineIntegrityError(
                "Customers cannot enter arbitrary module names on this product line",
                "arbitrary_module_not_allowed",
            )


def validate_ready_solution_record(*, product_line: str, solution_id: int | None, package_id: int | None) -> None:
    require_product_line(product_line)
    if product_line != PRODUCT_LINE_READY_SOLUTION:
        raise ProductLineIntegrityError(
            "Expected product_line=ready_solution",
            "wrong_product_line",
        )
    if not solution_id or not package_id:
        raise ProductLineIntegrityError(
            "Ready Solution records require a vertical solution and package",
            "missing_vertical_template",
        )


def validate_helpers_cloud_record(
    *,
    product_line: str,
    cloud_plan_id: int | None,
    github_repository: str | None = None,
    arbitrary_module: str | None = None,
    solution_id: int | None = None,
) -> None:
    require_product_line(product_line)
    if product_line != PRODUCT_LINE_HELPERS_CLOUD:
        raise ProductLineIntegrityError(
            "Expected product_line=helpers_cloud",
            "wrong_product_line",
        )
    if not cloud_plan_id:
        raise ProductLineIntegrityError(
            "Helpers ERP Cloud records require a Cloud plan",
            "missing_cloud_plan",
        )
    if solution_id is not None:
        raise ProductLineIntegrityError(
            "A vertical solution template cannot be selected as a generic Cloud package",
            "vertical_template_not_cloud_package",
        )
    reject_github_on_customer_order(
        product_line=product_line,
        github_repository=github_repository,
        arbitrary_module=arbitrary_module,
    )


def validate_developer_platform_record(
    *,
    product_line: str,
    cloud_plan_id: int | None = None,
    cloud_package_id: int | None = None,
) -> None:
    require_product_line(product_line)
    if product_line != PRODUCT_LINE_DEVELOPER_PLATFORM:
        raise ProductLineIntegrityError(
            "Expected product_line=developer_platform",
            "wrong_product_line",
        )
    if cloud_plan_id is not None or cloud_package_id is not None:
        raise ProductLineIntegrityError(
            "Developer Platform builds cannot be treated as a customer Cloud package",
            "cloud_fields_on_platform",
        )


def assert_owner(record_user_id: int | None, session_user_id: int) -> None:
    if record_user_id != session_user_id:
        raise ProductLineIntegrityError("Not found", "not_owner")
