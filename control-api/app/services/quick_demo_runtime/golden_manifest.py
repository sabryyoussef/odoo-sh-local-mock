"""QD1-F2 golden artifact manifest validation.

Schema is intentionally strict: anything missing/invalid fails closed.
This module has zero network/process dependencies — it only validates a
manifest dict against the expected contract.
"""
from __future__ import annotations

SCHEMA_VERSION = "qd1-golden-v1"

REQUIRED_TOP = {
    "schema_version",
    "solution_code",
    "edition",
    "odoo_version",
    "community_source_digest",
    "hms_source_digest",
    "module_list",
    "database_template",
    "database_fingerprint",
    "filestore_snapshot",
    "filestore_checksum",
    "build_timestamp",
    "builder_identity",
    "cron_disabled",
    "outbound_integrations_disabled",
}

REQUIRED_MODULES = ("acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix", "acs_hms_dashboard")


class ManifestError(ValueError):
    pass


class GoldenManifest:
    def __init__(self, raw: dict) -> None:
        if not isinstance(raw, dict):
            raise ManifestError("manifest must be an object")
        missing = REQUIRED_TOP - set(raw)
        if missing:
            raise ManifestError(f"missing manifest keys: {sorted(missing)}")
        if raw["schema_version"] != SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported schema_version {raw['schema_version']!r}; "
                f"expected {SCHEMA_VERSION!r}"
            )
        if raw["solution_code"] != "hms":
            raise ManifestError("solution_code must be 'hms'")
        if raw["edition"] != "community":
            raise ManifestError("edition must be 'community'")
        if not isinstance(raw["module_list"], (list, tuple)):
            raise ManifestError("module_list must be a list")
        modules = {str(m) for m in raw["module_list"]}
        missing_mods = set(REQUIRED_MODULES) - modules
        if missing_mods:
            raise ManifestError(f"missing required HMS modules: {sorted(missing_mods)}")
        if raw.get("cron_disabled") is not True:
            raise ManifestError("golden manifest must declare cron_disabled=true")
        if raw.get("outbound_integrations_disabled") is not True:
            raise ManifestError(
                "golden manifest must declare outbound_integrations_disabled=true"
            )
        for key in (
            "database_template",
            "database_fingerprint",
            "filestore_snapshot",
            "filestore_checksum",
            "build_timestamp",
            "builder_identity",
            "community_source_digest",
            "hms_source_digest",
            "odoo_version",
        ):
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise ManifestError(f"manifest.{key} must be a non-empty string")
        self.raw = raw

    @property
    def database_template(self) -> str:
        return str(self.raw["database_template"])

    @property
    def database_fingerprint(self) -> str:
        return str(self.raw["database_fingerprint"])

    @property
    def filestore_snapshot(self) -> str:
        return str(self.raw["filestore_snapshot"])

    @property
    def filestore_checksum(self) -> str:
        return str(self.raw["filestore_checksum"])

    @property
    def module_list(self) -> tuple[str, ...]:
        return tuple(str(m) for m in self.raw["module_list"])

    def to_dict(self) -> dict:
        return dict(self.raw)
