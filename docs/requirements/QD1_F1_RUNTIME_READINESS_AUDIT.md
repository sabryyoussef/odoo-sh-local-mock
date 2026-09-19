# QD1-F1 — Runtime Readiness Audit

**Date:** 2026-09-19
**Checkpoint:** read-only discovery only
**Method:** live host inventory of `/opt/projects/active/odoo-sh-local-mock` and `master` host.
No mutation, no secret retrieval, no destructive action.

## 1. Runtime host identity

| Item | Finding | Basis |
|---|---|---|
| Candidate host | `master` (this machine) | VERIFIED: `hostname` |
| Tailscale hostname | `master.tailcf9988.ts.net` | VERIFIED: `tailscale status` |
| Tailscale IPv4 | `100.76.217.35` | VERIFIED: `tailscale status --self` |
| Kernel / arch | Linux 7.0.0-29-generic x86_64 | VERIFIED |
| Role | Same host running `odoo-sh-local-mock` stack (control-api, provisioning-worker, build-postgres, tenants) | VERIFIED |
| Dedicated runtime host | Not a separate VM; runtime host is `master` itself. | VERIFIED — no `helpers-erp-01` VM observed; all tenant containers run on this host. |

## 2. CPU / RAM / Storage

| Resource | Finding | Basis |
|---|---|---|
| CPU count | 20 (nproc) | VERIFIED |
| Total RAM | 16 GiB (15Gi usable) | VERIFIED |
| Available RAM | 6.3 GiB (after caches) | VERIFIED |
| Swap | 8 GiB (7.9 GiB used — swap pressure) | VERIFIED |
| Primary storage | 439 GiB (`/dev/sda3`), 91 GiB free (79% used) | VERIFIED |
| Tenant data location | `/opt/projects/active/odoo-sh-local-mock/data/tenants/` | VERIFIED |
| Build/pg data | Same filesystem (`/dev/sda3`) | VERIFIED |
| Headroom for 3 slots | Possible but constrained — 91 GiB free, ~22 MB/template DB but full container image + runtime per slot | INFERRED |

## 3. Docker runtime

| Item | Finding | Basis |
|---|---|---|
| Docker version | 29.6.0 | VERIFIED |
| Running containers | 127 total (via `docker ps -a`) | VERIFIED |
| Active HMS tenants | 4: `mosh-tenant-hms_6_725292`, `_28_74d22b`, `_29_0150ad`, `_33_f18d49` | VERIFIED |
| Tenant image | `odoo:19.0` for all observed tenants | VERIFIED |
| Tenant pattern | One container, one DB role/database/filestore/config/hostname, per-tenant loopback or dedicated port | VERIFIED |
| Networks | `odoo-sh-local-mock_default` (bridge), plus many per-project networks | VERIFIED |

## 4. PostgreSQL

| Item | Finding | Basis |
|---|---|---|
| Server version | PostgreSQL 18.6 | VERIFIED (`psql --version`) |
| Runtime model | Container `odoo-sh-local-mock-build-postgres-1` (postgres:16-alpine) | VERIFIED |
| Admin role | `mosh_admin` (POSTGRES_USER); `postgres` role does not exist | VERIFIED |
| Tenant DB prefix | `mosh_tnt_` | VERIFIED |
| Template DB prefix | `mosh_tpl_` | VERIFIED |
| Active HMS tenant DBs | `mosh_tnt_hms_6_725292`, `_28_74d22b`, `_29_0150ad`, `_33_f18d49` | VERIFIED |
| Total databases | 43 non-template | VERIFIED |

## 5. Routing / TLS layer

| Item | Finding | Basis |
|---|---|---|
| Reverse proxy | Nginx on `master` (`192.168.1.5:80` and `:443`) | VERIFIED |
| Caddy | NOT INSTALLED | VERIFIED (`caddy` not found) |
| Trusted public domain | `sabry.serveirc.com` | VERIFIED (nginx `server_name`) |
| Certificate | Self-signed, CN=`sabry.serveirc.com`, O=Master Server, C=EG, valid until 2027-06-23 | VERIFIED |
| Wildcard certificate | **NO** — cert is single-name, no SAN, no wildcard | VERIFIED |
| Wildcard server_name | nginx uses `*.sabry.serveirc.com` server block, but cert does not cover subdomains | VERIFIED |
| Existing tenant subdomains | `*.sabry.serveirc.com` with `{tenant-code}.sabry.serveirc.com` pattern | VERIFIED |
| Tailscale DNS | Enabled; `master.tailcf9988.ts.net` | VERIFIED |
| Tailscale Funnel | Active on `master.tailcf9988.ts.net:443`, `:10081`, `:10443`, `:10000` | VERIFIED |
| Public IPv4 | Not directly exposed (LAN `192.168.1.5` + Tailscale `100.76.217.35`) | VERIFIED |
| Candidate QD hostname | `qd-<public_id>.<trusted-domain>` — `sabry.serveirc.com` is the only existing trusted domain | INFERRED — no wildcard cert means new subdomains need cert provisioning or Tailscale serve/funnel |

## 6. Existing HMS Community source

| Item | Finding | Basis |
|---|---|---|
| HMS host module path | `/opt/projects/active/odoo-sh-local-mock/data/hms-modules/` | VERIFIED |
| Host path contents | **EMPTY** — no HMS modules on disk | VERIFIED |
| HMS container mount path | `/mnt/hms-addons` (per `hms_tenant_provisioner.py`) | VERIFIED (code) |
| Required modules contract | `acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`, `acs_hms_dashboard` | QD1-D §6 |
| Modules in `mosh_tpl_hms_v1_0_0_demo` | `api_doc auth_passkey auth_totp base base_import base_import_module base_setup bus html_editor iap rpc web web_tour web_unsplash` — **NO HMS modules** | VERIFIED |
| Modules in live HMS tenant `hms_6_725292` | `account api_doc auth_passkey auth_totp base base_import base_import_module base_setup bus contacts hr html_editor iap mail maintenance purchase rpc stock web web_tour web_unsplash` — **NO acs_hms modules** | VERIFIED |
| `hms-modules` source availability | **BLOCKED** — required HMS modules are not present on disk and not in any existing DB | VERIFIED |

## 7. Golden database candidate

| Item | Finding | Basis |
|---|---|---|
| Candidate name | `mosh_tpl_hms_v1_0_0_demo` (only existing `mosh_tpl_*` with "hms" in name) | VERIFIED |
| Candidate size | 22 MB | VERIFIED |
| Template status | Listed in pg but NOT marked as `datistemplate`; `template0` and `template1` exist | VERIFIED |
| Usability as golden | **INSUFFICIENT** — lacks HMS modules. A golden Community HMS clone source must have `acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`, `acs_hms_dashboard` installed and verified. | VERIFIED (negative) |

## 8. Port / capacity landscape

| Item | Finding | Basis |
|---|---|---|
| Config port range | `tenant_port_min=8201`, `tenant_port_max=8298` | VERIFIED (config.py) |
| Free ports 8201-8205 | All free | VERIFIED |
| Ports in use (tenant) | 8201-8221 (mixed; some assigned to clone/vet/hms/sis/p3 tenants) | VERIFIED |
| 3-slot port availability | Plenty (8230-8234 free, range extends to 8298) | VERIFIED |
| Proposed initial capacity | **3** (per QD1-D contract default, pending golden DB availability) | INFERRED |

## 9. Filestore snapshot candidate

| Item | Finding | Basis |
|---|---|---|
| Tenant filestore pattern | `/data/tenants/{solution}_{tenant_id}/filestore/` | VERIFIED |
| Template filestores on disk | No `template_filestores/` directory exists | VERIFIED (absent) |
| Filestore snapshot source | **UNKNOWN** — no controlled golden filestore snapshot exists; would need to be derived from a verified golden DB's data directory | BLOCKED |

## 10. Runtime policy environment

| Item | Finding | Basis | 
|---|---|---|
| Existing HMS provisioning code | `hms_tenant_provisioner.py` (ACS/HMS module config), `tenant_docker_service.py`, `tenant_postgres_service.py`, `cloud_demo_clone_service.py` | VERIFIED (code) |
| Clone locking | `tenant_postgres_service.py` has template clone but no advisory lock / connection drain | VERIFIED (QD1-D finding — still current) |
| Cron policy | `without_demo=True` in conf but no `max_cron_threads=0` | VERIFIED (container config inspection) |
| Control plane DB | `/data/control.db` (SQLite, 357 MB) with `tenants` table including `public_url`, `domain`, `database_role`, `container_name`, `http_port` | VERIFIED |

## 11. Gaps / blockers

| Gap | Severity | Notes |
|---|---|---|
| No HMS Community source on disk | **BLOCKED** for golden build | `data/hms-modules/` is empty; modules required: `acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`, `acs_hms_dashboard` |
| No valid golden HMS database | **BLOCKED** | `mosh_tpl_hms_v1_0_0_demo` exists but has zero HMS modules |
| No golden filestore snapshot | **BLOCKED** | No controlled HMS filestore snapshot exists |
| No wildcard TLS certificate | **BLOCKED** for `*.sabry.serveirc.com` HTTPS | Cert is single-name CN=`sabry.serveirc.com`; subdomains would get cert errors. Tailscale serve/funnel or a wildcard Let's Encrypt cert would be needed. |
| No dedicated runtime VM | **NOTE** | Runtime host is `master` itself, not a separate Proxmox VM. QD1-D assumes dedicated runtime host; using the existing control-plane host is acceptable but must be stated. |
| Swap pressure | **NOTE** | 7.9/8 GiB swap used; provisioning 3 concurrent containers risks OOM without memory limits. |
| Storage at 79% | **NOTE** | 91 GiB free; golden DB (22 MB) + 3 filestores (~36 KB each current, but real golden would be larger) — monitor. |

## 12. Verdict

- Runtime host (`master`) is identified and has enough free ports and RAM headroom (with limits) for **3 bounded HMS Community Quick Demo slots**.
- **Golden artifact creation is the critical blocker.** No valid Community HMS template database or filestore snapshot exists in the current environment. A golden build is a prerequisite for QD1-F3 and is **not authorized** in this checkpoint.
- DNS/routing for `qd-<public_id>.<trusted-domain>` is feasible via `*.sabry.serveirc.com` nginx config, but **TLS must be resolved** (wildcard cert, Tailscale serve, or Tailscale Funnel) before any public launch.
- The existing HMS tenant infrastructure proves the per-session container/DB/role/config/mount pattern works; QD1-F2 can model the real adapter on these proven components but must add: advisory clone locking, connection draining, per-slot ownership enforcement, and strict cleanup.

