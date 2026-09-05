# Phase 5 recovery — control DB schema drift

Compared live `data/control.db` to pre-canary backup `data/control.db.backup.20260905T035704Z_p3_preflight`.

## Result

Tables added/removed: **none**

Column drift: **one table**

### `cloud_subscriptions` — additive nullable DATETIME columns

| Column | Pre-canary | Live | Committed main `CloudSubscription` (`73e75b9` models.py) |
| --- | --- | --- | --- |
| `trial_ends_at` | absent | DATETIME NULL | present |
| `suspended_at` | absent | DATETIME NULL | present |
| `grace_ends_at` | absent | DATETIME NULL | present |
| `terminated_at` | absent | DATETIME NULL | present |

All four values are NULL on subscriptions 1, 2, and 3.

`control-api/app/migrate.py` on both main and P3 does **not** currently `_add_column` these four onto `cloud_subscriptions` (it does add similar columns on `cloud_instances`). They were applied during the failed canary when P3/live ORM touched the live file. They match the committed SQLAlchemy model on main.

## Classification

**Harmless forward-compatible additions. Retain. Do not DROP.**

They do not break committed main: the live schema now matches `models.py`. Dropping them would make eligibility code that reads `sub.suspended_at` / `sub.terminated_at` fail on live SQLite. No destructive schema surgery in this job.

`migrate.py` still does not add them. A later additive migration on main would make the change explicit; that is out of scope here.

## Integrity

Live `PRAGMA integrity_check` = ok at backup (`20260905T055644Z`) and after extra rollback (`2026-09-05T06:02:42Z`).
