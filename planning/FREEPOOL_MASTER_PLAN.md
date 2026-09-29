# FreePool — master plan

**Status:** charter accepted · foundation `freellmpool` @ 0.13.0 · first client Hermes  
**Workspace note:** this repository is Helpers ERP / Mock Odoo.sh; FreePool lives in `/freepool` as an deployable bundle and is intended to graduate to its own repository once Hermes + Tailscale paths are proven.

## Charter (authoritative)

| Pillar | Requirement |
|--------|-------------|
| **Primary goal** | Maximum **free** provider diversity (breadth of distinct free tiers, not one hot provider). |
| **Foundation** | [freellmpool](https://github.com/0xzr/freellmpool) — OpenAI-compatible pool, catalog, quota, failover, MCP, Tailscale serve. |
| **References** | Free LLM Gateway patterns, FreeLLMAPI catalogs, LiteLLM-style unified routing (conceptual only; no LiteLLM runtime dependency). |
| **Discovery** | External free-provider catalogs are **advisory**; local `providers.toml` + verification gates remain executable truth. |
| **Routing** | **Capability + health + quota + latency** (weighted ordering, then failover walk). |
| **Tools** | **Verified only** — tool-calling routes must pass conformance / live probes before auto-routing. |
| **Failure** | Automatic cross-provider failover (429, 5xx, transport, empty reply where configured). |
| **Agent continuity** | Sticky session + context handoff across failover (see gap §4). |
| **Paid** | **Never automatic** — no silent fallback to metered/paid upstreams. |
| **Clients** | Hermes first; Cursor / OpenCode / Odoo agents later. |
| **Security** | Localhost + Tailscale first; bearer proxy key on any non-loopback bind. |
| **Multi-key** | Allowed only where provider ToS permits (one human account per key; no credential sharing). |
| **Final review** | Independent Cursor red-team before production-ish exposure. |

## Foundation mapping (what freellmpool already gives)

freellmpool 0.13.0 already implements most of the data plane:

| Charter need | freellmpool mechanism |
|--------------|----------------------|
| Provider diversity | Large audited `providers.toml` catalog; `spread` routing mode buckets usage across providers (`router.py`, `_SPREAD_BUCKET`). |
| Capability | `capability.py`, `capability_scores.json`, difficulty-aware penalties in quality/agent modes. |
| Health | `route_health.py`, failure classification in `router._is_health_failure`. |
| Quota | `quota.py`, per-day budgets and cooldown after 429. |
| Latency | Smoothed latency stats in quality/spread/agent scoring (`router.py` `_QUALITY_SLOW_S`, metrics). |
| Failover | `Pool.ask` ordered walk until success or `AllProvidersExhausted`. |
| Hermes | `Profile(name="hermes")` in `profiles.py` — custom OpenAI endpoint → local proxy `/v1`. |
| Tailscale | `freellmpool tailnet serve` with session-scoped proxy keys. |
| Tools verified | `conformance.py` / feature requirements per route. |
| Paid never auto | Agent profiles use `cost_class="free"` for Hermes; pool only selects **enabled free-tier routes** the operator configured — no paid upstream unless explicitly wired (e.g. Codex profile is separate). |

**Routing default for FreePool:** `spread` — optimizes for diversity (least-used provider tiers first), then health/latency within tier. Alternative: `agent` for long tool loops on strongest free capability tier.

## Architecture

```text
┌─────────────┐     OpenAI /v1      ┌──────────────────┐
│ Hermes      │ ──────────────────► │ freellmpool      │
│ (custom ep) │                     │ proxy :8080      │
└─────────────┘                     └────────┬─────────┘
                                             │
                    capability + health + quota + latency
                                             │
                     ┌───────────────────────┼───────────────────────┐
                     ▼                       ▼                       ▼
               Groq / Cerebras          Gemini / OpenRouter        keyless tiers
               (configured keys)         (configured keys)          (Pollinations, …)
```

**Control plane (FreePool-specific, incremental):**

1. **Policy file** — `freepool/config.toml` sets routing mode, proxy auth, cooldown; documents free-only posture.
2. **Catalog hygiene** — periodic sync from public free-LLM lists → human review → enable in `providers.toml` (upstream freellmpool workflow / PRs).
3. **Continuity layer (gap)** — session stickiness + handoff (§4).

## Gap analysis

### 4.1 Sticky session + context handoff (P0 for agent continuity)

**Problem:** On failover, a new provider/model may break tool schemas or “forget” implicit state even when messages are replayed.

**Target behavior:**

- Client sends stable `X-FreePool-Session: <uuid>` (Hermes: middleware or env).
- Router prefers last-success `(provider, model)` for that session while health/quota allow.
- On forced failover, emit structured handoff metadata (provider change, model change, truncated token estimate) in response headers or OpenAI `system_fingerprint` extension for the agent to reconcile.
- Optional: compress handoff summary via one cheap free model (explicit opt-in, still free-only).

**Implementation locus:** freellmpool `proxy.py` + `router.py` (upstream contribution) or FreePool fork; not duplicated in Helpers ERP repo.

### 4.2 Catalog discovery automation (P1 diversity)

- Ingest advisory entries from:
  - [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)
  - [xyzs996/free-llm-api](https://github.com/xyzs996/free-llm-api)
  - [freellms/free-llm-api-keys](https://github.com/freellms/free-llm-api-keys)
- **Never** auto-enable: diff → verification job → maintainer approve.
- Align with freellmpool PR #7 direction (`capacity status`, `providers health`).

### 4.3 Client matrix (P1)

| Client | Integration | Status |
|--------|-------------|--------|
| Hermes | `freellmpool profile hermes` / custom endpoint | **Now** — see `freepool/README.md` |
| OpenCode | `profile opencode` | After npm registry publish (freellmpool 0.13 notes pending) |
| Cursor | Custom OpenAI base URL + local proxy | Document; same proxy |
| Odoo / Helpers agents | HTTP OpenAI client to Tailscale URL | Later; reuse proxy + org proxy key |

### 4.4 Security (P0)

- Bind `127.0.0.1` by default.
- Tailscale: `freellmpool tailnet serve --port 8080` + generated bearer key.
- No public ingress without Cloudflare Access or equivalent (out of scope for v0).

### 4.5 Red-team review (P0 gate)

Before calling FreePool “team-ready”:

- Cursor-independent agent reviews: auth bypass, key leakage in logs, SSRF in provider URLs, quota exhaustion DoS, prompt exfiltration to non-free routes.
- Evidence bundle in `docs/freepool/red-team/` (future).

## Phased delivery

### Phase 0 — Hermes on local pool (this repo)

- [x] Charter + architecture doc (this file)
- [x] `freepool/` docker + config examples
- [ ] Operator runs proxy + Hermes smoke (`freellmpool ask`, one Hermes turn)
- [ ] Optional: contribute sticky-session design upstream

### Phase 1 — Diversity ops

- Catalog sync playbook + weekly health cron (`freellmpool providers health`)
- Document multi-key policy per provider ToS

### Phase 2 — Additional clients

- OpenCode/Cursor docs; Odoo agent hook via existing Hermes/OmniRoute patterns in Helpers ERP (reference only)

## Non-goals

- Hosting a public multi-tenant SaaS gateway on Helpers ERP infrastructure
- Automatic paid provider usage
- Replacing local Ollama/LM Studio (pin-only, explicit import)

## Commands (operator)

```bash
cd freepool
cp .env.example .env   # add free-tier keys
docker compose up -d
freellmpool profile hermes --doctor
# Point Hermes at http://127.0.0.1:8080/v1
```

## References

- freellmpool README: https://github.com/0xzr/freellmpool
- Hermes profile: `freellmpool profile show hermes`
- Routing modes: `fair`, `fast`, `quality`, `agent`, `spread`
