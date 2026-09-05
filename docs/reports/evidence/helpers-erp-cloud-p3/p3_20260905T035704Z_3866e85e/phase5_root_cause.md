# Phase 5 recovery — root cause

Failed run: `p3_20260905T054700Z_9340f78e`  
Request: 3

## Hypothesized race (recorded, not treated as proven)

The Phase 5 failure note hypothesized:

1. P3 files were copied onto the live compose bind mount (`./control-api/app`).
2. The pre-existing `provisioning-worker` consumed request 3.
3. The bounded runner then saw a request already in `provisioning`.

## What timestamps and logs actually show

| UTC | Evidence | Interpretation |
| --- | --- | --- |
| 2026-09-02T11:15:54Z | compose worker `StartedAt` | Worker process loaded `worker_main.py` long before P3. Bind-mount copy later does **not** reload in-memory code. |
| 2026-09-05T05:47:04Z | request/order/subscription/instance/user 9 created | Canary commercial rows inserted. |
| 2026-09-05T05:47:05Z | audit 539 `cloud_provisioning_approved` | Operator approval. |
| ~05:47–05:48 | Phase 5 runner progress: `invalid_status` expected queued, got `provisioning`; `claimed_by=provisioning-worker-1` | First real execute failed the adapter queued-only gate. Compose worker logs in this window do **not** show a cloud claim. |
| 2026-09-05T05:48:56Z | audit 540 `cloud.p2.rollback` `rollback_completed` | First rollback after invalid_status (no tenant reserved in audit). |
| 2026-09-05T05:49:07Z | audit 541 tenant reserved `p2_p3_20260905t054700z_9340_3_d50c65` | Second attempt after adapter patch. |
| 2026-09-05T05:49:18Z | audit 542 provisioned ready, `http_port=8216` | Brief ready; HTTP/Odoo proof files were never written. |
| 2026-09-05T05:50:15Z | audit 543 rollback_completed; request 3 `rolled_back` | Intentional canary cleanup. |
| 2026-09-05T05:53:29–30Z | compose worker SIGTERM / heartbeat `stopped` | Worker stopped after the canary window. `docker logs --since 05:40Z` contains **only** shutdown lines — no "Checking Helpers Cloud queue" / "Cloud job claimed". |
| after 05:50 | Roo job `2c031493-…` `process_failed` exit 1 | OmniRoute HTTP 503 and `ui_messages.json` write failure. Phase 5 Roo task dir `025ec237-…` is empty (no `ui_messages.json`). |

Compose `docker-compose.yml` does not set `HELPERS_CLOUD_REAL_PROVISIONING_ENABLED`. Even a reloaded P3 `worker_main` would fail-closed (`_should_process_cloud` requires enabled **and** `max_jobs > 0`).

`run_bounded_cloud_worker` defaults `worker_id` to `settings.provisioning_worker_id` (`provisioning-worker-1`). `claim_next_real_cloud_job` then sets `status=provisioning` **before** `provision_cloud_request`, which at P3 HEAD still required `queued`. That produces `invalid_status` **without** a second process racing.

**Conclusion:** bind-mount copy of P3 files **did** leak onto main (proven by hashes). Consumption of request 3 by the **long-running compose worker** is **not** supported by that worker's logs. The `claimed_by=provisioning-worker-1` + `invalid_status` sequence is explained by the bounded runner using that worker id plus the queued-only adapter gate. The uncommitted adapter patch (accept `provisioning`) is a genuine P3 integration fix for that gate, and would also mask a true two-process race — so retry must still keep the live compose worker stopped and must not copy P3 onto the live bind mount.

## OmniRoute 503 and `ui_messages.json`

Recorded separately from the provisioning failure:

- Happened **after** request 3 was already `rolled_back` (05:50:15Z) while the Roo/bridge process was still writing evidence.
- Phase 5 Roo storage dir has no `ui_messages.json` (write failed).
- `control-api` logs in the same window show only `GET /cloud` and `/cloud/pricing` HTTP 200. No schema/API write attributable to 503.
- **No project or runtime mutation** from OmniRoute 503 / ui_messages write failure. Those are operator-bridge failures, not Odoo/control-plane mutations.

## P3 worktree uncommitted fixes (preserved, not copied to main)

1. Adapter status gate: accept `queued` **or** `provisioning` — genuine (claim already transitions to provisioning). Focused tests added on the P3 worktree (3 passed). **Not committed** pending review.
2. `_redacted(msg: str = "")` — genuine (calls use kwargs only). Test added. **Not committed** pending review.

Neither change is a generated artifact. They remain uncommitted on `/tmp/p3-helpers-erp-cloud-p3`.
