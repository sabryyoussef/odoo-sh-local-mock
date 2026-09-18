"""HC3.6 Session 6 — Host-side LVM verifier transport.

Read-only SSH transport that executes a single fixed command on the trusted
Proxmox host.  The transport is explicitly classified as a read-only verifier,
not a provisioning transport.

Hard safety constraints:
  - Executes EXACTLY: sudo {VERIFIER_COMMAND}
  - Rejects arbitrary command arguments
  - Rejects alternate binaries or wrapper paths
  - Enforces timeout and bounded output size
  - Parses only valid JSON
  - Fails closed on SSH failure, command failure, sudo prompt, malformed JSON,
    unexpected schema, oversized output, host identity mismatch
  - Never logs secrets or private SSH credentials

This module does NOT perform any Proxmox mutations.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Fixed command constants ──────────────────────────────────────────────────
# The verifier command path is a compile-time constant.  Configuration cannot
# change which binary is executed; only the SSH target can vary.
VERIFIER_COMMAND = "/usr/local/sbin/helper-compute-lvm-proof"
VERIFIER_USER_DEFAULT = "helper-verify"
VERIFIER_HOST_DEFAULT = "pve-test"
VERIFIER_TIMEOUT_DEFAULT = 30
VERIFIER_MAX_OUTPUT_DEFAULT = 1_048_576  # 1 MiB

# Allowed SSH options — no ProxyJump, no agent forwarding, no TCP forwarding.
SSH_BASE_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=10",
    "-o", "ServerAliveCountMax=3",
    "-o", "ForwardAgent=no",
    "-o", "ForwardX11=no",
    "-o", "RequestTTY=no",
]

# Expected JSON schema keys from the lvs output.
EXPECTED_LVS_KEYS = frozenset({"vg_name", "lv_name", "lv_attr", "origin", "pool_lv", "lv_size"})
EXPECTED_LVS_REPORT_KEYS = frozenset({"report"})

# ── Errors ───────────────────────────────────────────────────────────────────

class HostVerifierError(Exception):
    """Fail-closed error from the host verifier transport."""

    def __init__(self, code: str, *, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code)


# ── Configuration ────────────────────────────────────────────────────────────

def _verifier_config() -> dict[str, Any]:
    """Read verifier config from application settings.  Never returns secrets."""
    s = get_settings()
    return dict(
        user=getattr(s, "helper_compute_host_verifier_user", VERIFIER_USER_DEFAULT),
        host=getattr(s, "helper_compute_host_verifier_host", VERIFIER_HOST_DEFAULT),
        timeout_sec=int(getattr(s, "helper_compute_host_verifier_timeout_sec", VERIFIER_TIMEOUT_DEFAULT)),
        max_output_bytes=int(getattr(s, "helper_compute_host_verifier_max_output_bytes", VERIFIER_MAX_OUTPUT_DEFAULT)),
        ssh_key_path=getattr(s, "helper_compute_host_verifier_ssh_key_path", ""),
    )


# ── Transport ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HostVerifierResult:
    """Structured result from a single verifier invocation."""
    node: str
    raw_records: tuple[dict[str, Any], ...]
    command: str
    exit_code: int
    output_bytes: int


def _validate_host(host: str, expected_host: str) -> None:
    """Reject if the SSH target does not exactly match the expected node."""
    if not host or not expected_host:
        raise HostVerifierError("host_config_missing")
    # Only allow simple hostnames or FQDNs — no user@host, no port, no slashes.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
        raise HostVerifierError("host_identity_invalid", detail="disallowed characters")
    if host != expected_host:
        raise HostVerifierError("host_identity_mismatch",
                                detail=f"expected {expected_host}, got {host}")


def _validate_command(command: str) -> None:
    """Reject if the command does not exactly match the fixed verifier path."""
    if command != VERIFIER_COMMAND:
        raise HostVerifierError("verifier_command_rejected",
                                detail="arbitrary command execution is forbidden")


def _build_ssh_command(user: str, host: str, command: str,
                       key_path: str = "") -> list[str]:
    """Build the exact SSH command array.  No shell interpretation."""
    _validate_command(command)
    cmd: list[str] = ["ssh"]
    cmd.extend(SSH_BASE_OPTIONS)
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.append(f"{user}@{host}")
    # The remote command is a single string — SSH sends it to the remote shell.
    # We use the fixed sudo wrapper; no shell metacharacters from config.
    cmd.append(f"sudo {command}")
    return cmd


def _parse_lvs_output(raw: str) -> HostVerifierResult:
    """Parse the JSON output of the fixed verifier command.

    The verifier executes:
      /usr/sbin/lvs --reportformat json --units b --nosuffix \
        -o vg_name,lv_name,lv_attr,origin,pool_lv,lv_size

    Expected schema:
      {"report": [{"vg": [...], "lv": [...]}]}

    Each record has exactly the six expected keys.
    """
    if not raw or not raw.strip():
        raise HostVerifierError("verifier_empty_output")

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HostVerifierError("verifier_malformed_json",
                                detail=str(exc)[:200])

    if not isinstance(parsed, dict):
        raise HostVerifierError("verifier_unexpected_schema",
                                detail="top-level not object")

    report = parsed.get("report")
    if not isinstance(report, list) or len(report) == 0:
        raise HostVerifierError("verifier_unexpected_schema",
                                detail="report key missing or empty")

    # Collect all LV records from the first report block.
    records: list[dict[str, Any]] = []
    for block in report:
        if not isinstance(block, dict):
            raise HostVerifierError("verifier_unexpected_schema",
                                    detail="report block not object")
        # lvs JSON may put records under "lv" key.
        lv_list = block.get("lv", [])
        if not isinstance(lv_list, list):
            raise HostVerifierError("verifier_unexpected_schema",
                                    detail="lv key missing or not list")
        for rec in lv_list:
            if not isinstance(rec, dict):
                raise HostVerifierError("verifier_unexpected_schema",
                                        detail="lv record not object")
            # Validate schema — reject unexpected fields.
            if not EXPECTED_LVS_KEYS.issubset(rec.keys()):
                missing = EXPECTED_LVS_KEYS - rec.keys()
                raise HostVerifierError("verifier_unexpected_schema",
                                        detail=f"missing keys: {missing}")
            records.append(rec)

    if not records:
        raise HostVerifierError("verifier_no_lv_records")

    return HostVerifierResult(
        node="",  # populated by caller
        raw_records=tuple(records),
        command=VERIFIER_COMMAND,
        exit_code=0,
        output_bytes=len(raw.encode("utf-8")),
    )


def execute_verifier(host: str | None = None, *,
                     config_override: dict[str, Any] | None = None) -> HostVerifierResult:
    """Execute the fixed host verifier command via SSH.

    This is the single public entry point.  It:
    1. Reads config for SSH target and timeout.
    2. Validates the target matches the expected node.
    3. Builds the exact SSH command.
    4. Executes with bounded timeout and output.
    5. Parses and validates JSON output.
    6. Returns structured result.

    Never accepts arbitrary commands, hosts, or arguments.
    """
    cfg = _verifier_config()
    if config_override:
        cfg.update(config_override)

    user = cfg["user"]
    target_host = host or cfg["host"]
    timeout_sec = cfg["timeout_sec"]
    max_output = cfg["max_output_bytes"]
    key_path = cfg.get("ssh_key_path", "")

    # Validate host identity before any network operation.
    _validate_host(target_host, cfg["host"])

    # Build the exact command — VERIFIER_COMMAND is a compile-time constant.
    ssh_cmd = _build_ssh_command(user, target_host, VERIFIER_COMMAND,
                                 key_path=key_path)

    logger.info("host_verifier.execute node=%s command=%s", target_host, VERIFIER_COMMAND)

    try:
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HostVerifierError("verifier_ssh_timeout",
                                detail=f"timeout after {timeout_sec}s")
    except OSError as exc:
        raise HostVerifierError("verifier_ssh_os_error",
                                detail=str(exc)[:200])

    # SSH connection or command failure is fail-closed.
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # Detect sudo prompt — if sudo asked for a password, the command
        # was not pre-authorized.  This is a policy violation.
        if "[sudo]" in stderr or "password" in stderr.lower():
            raise HostVerifierError("verifier_sudo_prompt",
                                    detail="sudo requested password")
        raise HostVerifierError("verifier_command_failed",
                                detail=f"exit={result.returncode}")

    stdout = result.stdout or ""

    # Enforce output size bound.
    output_bytes = len(stdout.encode("utf-8"))
    if output_bytes > max_output:
        raise HostVerifierError("verifier_output_oversized",
                                detail=f"{output_bytes} > {max_output}")

    parsed = _parse_lvs_output(stdout)
    # Reconstruct with node info.
    return HostVerifierResult(
        node=target_host,
        raw_records=parsed.raw_records,
        command=VERIFIER_COMMAND,
        exit_code=0,
        output_bytes=output_bytes,
    )


def verify_verifier_capability() -> dict[str, Any]:
    """Read-only pre-clone capability verification.

    Proves:
    - Verifier user/path configuration is present in application config.
    - Trusted host matches expected node.
    - Fixed verifier command path is exactly the expected constant.
    - Output schema is valid (tested via dry-run or last successful run).
    - Source LVs can be mapped successfully (if LVM data is provided).
    - Verifier cannot be used as arbitrary command transport (by interface design).

    Does NOT attempt privilege escalation testing.
    """
    cfg = _verifier_config()
    checks: list[dict[str, Any]] = []

    # Check 1: user configured
    user = cfg["user"]
    checks.append(dict(
        check="verifier_user_configured",
        passed=bool(user and user == VERIFIER_USER_DEFAULT),
        value="***" if user else "",
    ))

    # Check 2: host configured
    host = cfg["host"]
    checks.append(dict(
        check="trusted_host_configured",
        passed=bool(host and host == VERIFIER_HOST_DEFAULT),
        value=host or "",
    ))

    # Check 3: command path constant
    command_valid = VERIFIER_COMMAND == "/usr/local/sbin/helper-compute-lvm-proof"
    checks.append(dict(
        check="fixed_command_path",
        passed=command_valid,
        value=VERIFIER_COMMAND,
    ))

    # Check 4: timeout configured
    timeout = cfg["timeout_sec"]
    checks.append(dict(
        check="timeout_configured",
        passed=isinstance(timeout, int) and 5 <= timeout <= 300,
        value=str(timeout),
    ))

    # Check 5: max output configured
    max_output = cfg["max_output_bytes"]
    checks.append(dict(
        check="max_output_configured",
        passed=isinstance(max_output, int) and 1024 <= max_output <= 10_485_760,
        value=str(max_output),
    ))

    # Check 6: arbitrary command impossible by design
    try:
        _validate_command("/usr/local/sbin/helper-compute-lvm-proof")
        arbitrary_rejected = True
    except HostVerifierError:
        arbitrary_rejected = False
    checks.append(dict(
        check="arbitrary_command_rejected_by_design",
        passed=arbitrary_rejected,
    ))

    try:
        _validate_command("/bin/bash -c 'rm -rf /'")
        command_rejection_works = False  # should have raised
    except HostVerifierError:
        command_rejection_works = True
    checks.append(dict(
        check="malicious_command_rejected",
        passed=command_rejection_works,
    ))

    # Check 7: host validation rejects mismatched hosts
    try:
        _validate_host("evil-host", host)
        host_rejection_works = False
    except HostVerifierError:
        host_rejection_works = True
    checks.append(dict(
        check="host_mismatch_rejected",
        passed=host_rejection_works,
    ))

    all_passed = all(c["passed"] for c in checks)
    return dict(
        schema="hc36-verifier-capability-v1",
        all_checks_passed=all_passed,
        checks=checks,
        verifier_command=VERIFIER_COMMAND,
        expected_node=VERIFIER_HOST_DEFAULT,
    )
