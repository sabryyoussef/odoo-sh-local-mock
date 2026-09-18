"""Trusted HC3.6 execution boundary between compiled plan and clone transport.

Public operator surface is durable identifiers plus dry-run. There is no HTTP
route, worker hook, credential override, or generic Proxmox client. Real
mutation remains execute_real_clone() and stays disabled unless every gate
passes in a separately authorized session.
"""
from .real_clone_entry import (
    dry_run_real_clone,
    evaluate_plan_operations,
    execute_real_clone,
    inspect_isolated_lab,
)
from .real_clone_transport import (
    assert_frozen_mutation,
    intended_clone_request,
    live_readonly_probe,
)

__all__ = [
    'assert_frozen_mutation',
    'dry_run_real_clone',
    'evaluate_plan_operations',
    'execute_real_clone',
    'inspect_isolated_lab',
    'intended_clone_request',
    'live_readonly_probe',
]
