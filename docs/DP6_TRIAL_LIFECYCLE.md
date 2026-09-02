# DP6 trial lifecycle

Developer Platform trials follow an explicit UTC state machine. Billing is out of scope; `convert_trial_to_subscription` is a hook for DP7.

## Defaults

| Setting | Env | Default |
|---------|-----|---------|
| Trial length | `PLATFORM_TRIAL_DAYS` | 7 (1–90) |
| Grace | `PLATFORM_TRIAL_GRACE_DAYS` | 3 (0–30) |
| Retention after suspend | `PLATFORM_TRIAL_RETENTION_DAYS` | 30 (1–3650) |
| Automatic destructive terminate | `PLATFORM_TRIAL_AUTO_TERMINATE_ENABLED` | `false` |
| Warning offsets | `PLATFORM_TRIAL_WARN_DAYS` | `3,1` |
| Worker backoff | `PLATFORM_LIFECYCLE_BACKOFF_SEC` | 60 |
| Max attempts | `PLATFORM_LIFECYCLE_MAX_ATTEMPTS` | 5 |

Existing `trial_started_at` / `trial_ends_at` are never rewritten by DP6. New timestamps live on `platform_trial_lifecycles`.

## State machine

```
pending/deploying → trial_active → grace → suspension_pending → suspended
suspended → trial_active (reactivate) or converted
suspended → termination_pending → terminated
```

Failed deployment stays `failed` and is not expired by this worker.

Grace end is `trial_ends_at + PLATFORM_TRIAL_GRACE_DAYS`, stored once. Polling cannot extend it.

## Suspension / reactivation

Suspension stops the tenant container only. PostgreSQL database, role, filestore, backups, and port assignment are preserved. `suspended` is set only after the runtime is verified stopped.

Reactivation starts the existing container (or fails closed) and requires a health check. It does not create a second tenant.

## Conversion hook

`convert_trial_to_subscription(db, trial, actor=..., subscription_id=...)` records `converted_at` and blocks suspension. It does not create a subscription, invoice, or payment. If `subscription_id` is passed, the row must exist and belong to the same user. Tenant XOR (`customer_subscription_id` vs `platform_trial_id`) is unchanged until DP7 attaches a real subscription.

## Termination

Two steps: `termination_pending`, then `execute_termination`.

Destructive cleanup requires:

* unconverted trial
* `deployment_mode=platform_quick`
* `platform_trial_id` match and `customer_subscription_id` null
* retention deadline passed
* `PLATFORM_TRIAL_AUTO_TERMINATE_ENABLED` or explicit `operator_authorized=True`
* not a template DB (`mosh_tpl_*`)

## Worker

The provisioning worker runs a lifecycle tick **only when** no template, deployment, or solution provisioning job is claimed.

SQLite MVP: one worker, `next_lifecycle_retry_at` backoff. PostgreSQL later: `SELECT … FOR UPDATE SKIP LOCKED` on `platform_trial_lifecycles` (not implemented here).

## Schema

Additive tables `platform_trial_lifecycles` and `platform_trial_lifecycle_events` via `app/migrate_dp6.py` (not `migrate.py`, which has concurrent dirty work). Warnings use unique `event_key`; dispatch status is `recorded` (queued), never claimed as email sent.

## G3-C selectors

`platform/deploy/_lifecycle_panel.html`: `lifecycle-panel`, `lifecycle-state`, `lifecycle-countdown`, `lifecycle-expiry-warning`, `lifecycle-grace-warning`, `lifecycle-suspended-banner`, `lifecycle-reactivation-status`, `lifecycle-termination-status`.
