# G3-C lifecycle browser UAT

Isolated Playwright coverage for DP6 trial lifecycle. It reuses the G3-A compose project `mosh-e2e-g3a` and never points at live `data/control.db`.

## What is covered

Desktop Chromium (`lifecycle-chromium`) and Pixel 5 Chromium (`lifecycle-mobile`) exercise:

- Active countdown and non-negative remaining time
- 3-day / 1-day / expiry warnings without duplicate events or claimed email send
- Grace transition, stable grace deadline, no entitlement expansion
- Suspension after fake runtime stop, retained disposable DB/role/filestore/port/backup metadata
- Retryable failed stop
- Operator reactivation with health success, customer denial, health-failure retry
- Conversion hook with an explicit test subscription id (no invoices, payments, or new subscriptions)
- Termination eligibility, retention, operator authorization, fake cleanup, and protected tenants
- Ownership / CSRF / unauthenticated operator routes
- Accessible names, keyboard focus on the launch control, and no horizontal overflow on mobile

## Isolation

- Wrapper: `e2e.python.server:app` with `E2E_MODE=1`
- Disposable SQLite on tmpfs (`sqlite:////tmp/e2e-control.db`)
- Invalid `DOCKER_HOST`, no docker.sock, no workers
- Process-local fake runtime and clock; `install_isolated_lifecycle_controls()` refuses to load without `E2E_MODE=1`
- Generated credentials live only in gitignored `control-api/e2e/.auth/runtime.json`

## Commands

```bash
npm run test:e2e
```

Do not restart the live provisioning worker for this gate. Next recommended live step is a controlled DP6 activation: back up `control.db`, restart only the provisioning worker, and verify additive migration plus lifecycle heartbeat without changing the retained G2 trial.
