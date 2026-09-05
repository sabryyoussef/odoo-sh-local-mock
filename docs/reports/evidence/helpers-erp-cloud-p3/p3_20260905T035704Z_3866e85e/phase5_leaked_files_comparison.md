# Phase 5 recovery — leaked file comparison

Recovery TS: `20260905T055644Z`  
Main HEAD: `73e75b9b5882e1e6db66b66cde5c63a35f8127b9`  
P3 committed HEAD: `52e0d44d0676b1e351129053a4784bf4eb51a2a7`  
Pre-canary dirty list source: `preflight_manifest.json` (`timestamp_utc=2026-09-05T03:57:24.436872+00:00`)

Leaked-file backups: `recovery_leaked_files_20260905T055644Z/`

## Hash matrix (before restore)

| File | Live main | Main HEAD `73e75b9` | P3 HEAD `52e0d44` | P3 worktree (uncommitted) |
| --- | --- | --- | --- | --- |
| `control-api/app/config.py` | `f97049db…ccfce643` | `1d823cca…ec503b8` | `f97049db…ccfce643` | `f97049db…ccfce643` |
| `control-api/app/worker_main.py` | `e8f1ad6b…4001b6c4` | `431d8ecf…45a4cf5a` | `e8f1ad6b…4001b6c4` | `e8f1ad6b…4001b6c4` |
| `control-api/app/services/cloud_docker_adapter.py` | `81733195…ec722a` | `e0facbc0…82281efa` | `940a5a67…b1f5de9` | `81733195…ec722a` |
| `control-api/app/services/cloud_worker_service.py` | `3b2c784f…409248` | **not in main HEAD** | `8e48bd1b…1188f7` | `3b2c784f…409248` |

## Ownership proof

Pre-canary main dirt did **not** include these four paths. They appeared only after the failed Phase 5 canary copied P3 onto the live compose bind mount.

### `config.py`

Live == P3 HEAD == P3 worktree. Diff vs main HEAD is **+7 lines only**: fail-closed `helpers_cloud_real_provisioning_enabled`, `helpers_cloud_worker_max_jobs=0`, `helpers_cloud_run_id_prefix="p3_"`. No branding/i18n content.

### `worker_main.py`

Live == P3 HEAD == P3 worktree. Diff vs main HEAD is the P3 bounded cloud loop (`_should_process_cloud`, `run_bounded_cloud_worker`, `--cloud-bounded`, `claim_and_execute_one`). No branding/i18n content.

### `cloud_docker_adapter.py`

Live == P3 **uncommitted** worktree, not P3 HEAD. P3 HEAD vs live/worktree is the 2-line status-gate change (`queued` only → `queued or provisioning`). That change is a P3 adapter fix, not a user branding edit. Restoring main therefore returns the committed P2 adapter from `73e75b9`.

### `cloud_worker_service.py`

Untracked on main. Entire file is P3. Live == P3 uncommitted worktree (`_redacted(msg: str = "")`). Not present at main HEAD. Removed by exact path after backup.

## Restore method

Not used: `git reset`, `git checkout`, `git restore` on the whole worktree, `git clean`, stash, wildcard deletion.

Used:

1. `cp -a` each exact path into `recovery_leaked_files_20260905T055644Z/`.
2. `git show 73e75b9:<exact-path> > <exact-path>` for the three tracked files.
3. `rm` of the single untracked path `control-api/app/services/cloud_worker_service.py` after hash equality with the P3 worktree file.

After restore, the three tracked files match main HEAD SHA-256 exactly. The untracked file is absent.

## After-restore main porcelain

P3 leakage gone. Remaining dirt is the pre-existing branding/i18n set (templates, CSS, `branding.py`, `translations.py`, `view_context.py`, catalog tests, `docs/DEMO.md`, untracked landing partials) plus this evidence directory and `documentation/`.
