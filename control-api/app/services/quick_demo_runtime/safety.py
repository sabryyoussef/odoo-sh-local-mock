"""QD1-F2 real-runtime safety gates.

Decides whether the *real* adapter may execute at all. Every gate must
pass; absence fails closed. This function has NO side effects and does
NOT contact any external system — it only inspects configuration.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

_HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,}[a-z]{2,63}$")


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    code: str = "ok"


def _csv(value: str) -> frozenset[str]:
    return frozenset(v.strip() for v in (value or "").split(",") if v.strip())


def check_real_gates(settings, *, manifest_valid: bool) -> GateDecision:
    """Evaluate all F2 safety gates. Pure function of configuration."""
    if not settings.quick_demo_enabled:
        return GateDecision(False, "QUICK_DEMO_ENABLED is false", "feature_disabled")
    if not settings.quick_demo_community_hms_enabled:
        return GateDecision(False, "QUICK_DEMO_COMMUNITY_HMS_ENABLED is false", "community_disabled")
    if settings.quick_demo_max_active_sessions <= 0:
        return GateDecision(False, "QUICK_DEMO_MAX_ACTIVE_SESSIONS <= 0", "zero_capacity")
    if settings.quick_demo_adapter != "real":
        return GateDecision(False, "QUICK_DEMO_ADAPTER is not 'real'", "not_real_adapter")
    if not settings.quick_demo_real_enabled:
        return GateDecision(False, "QUICK_DEMO_REAL_ENABLED is false", "real_disabled")
    if not settings.quick_demo_real_hosts:
        return GateDecision(False, "QUICK_DEMO_REAL_HOSTS is empty", "no_runtime_hosts")
    if not settings.quick_demo_real_domains:
        return GateDecision(False, "QUICK_DEMO_REAL_DOMAINS is empty", "no_trusted_domains")
    for host in _csv(settings.quick_demo_real_hosts):
        if not _HOST_RE.fullmatch(host):
            return GateDecision(False, f"runtime host rejected: {host!r}", "bad_runtime_host")
    for domain in _csv(settings.quick_demo_real_domains):
        if not _DOMAIN_RE.fullmatch(domain):
            return GateDecision(False, f"trusted domain rejected: {domain!r}", "bad_domain")
    if not settings.quick_demo_real_golden_manifest:
        return GateDecision(False, "QUICK_DEMO_REAL_GOLDEN_MANIFEST path empty", "no_manifest_path")
    if not manifest_valid:
        return GateDecision(False, "golden manifest is not present/invalid", "manifest_invalid")
    if settings.quick_demo_real_ownership_schema_version != "qd1-golden-v1":
        return GateDecision(
            False,
            f"ownership schema version {settings.quick_demo_real_ownership_schema_version!r} unsupported",
            "ownership_schema_unsupported",
        )
    if not settings.quick_demo_mutation_token:
        return GateDecision(False, "QUICK_DEMO_MUTATION_TOKEN empty", "no_mutation_token")
    if settings.quick_demo_real_dry_run:
        return GateDecision(False, "QUICK_DEMO_REAL_DRY_RUN is true (no mutation in dry-run)", "dry_run")
    return GateDecision(True, "all real-runtime gates satisfied")
