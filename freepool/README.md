# FreePool deploy bundle

FreePool is a **policy and wiring layer** on top of [freellmpool](https://github.com/0xzr/freellmpool): maximize free provider diversity, route by capability + health + quota + latency, fail over automatically, and **never** silently use paid upstreams.

Full charter and roadmap: [../planning/FREEPOOL_MASTER_PLAN.md](../planning/FREEPOOL_MASTER_PLAN.md).

## Quick start (Hermes-first)

### 1. Install CLI (host)

```bash
python3 -m pip install --upgrade "freellmpool>=0.13.0"
freellmpool init --yes
```

Add free API keys to `~/.config/freellmpool/config.toml` or environment (see `.env.example`).

### 2. Copy FreePool policy

```bash
mkdir -p ~/.config/freellmpool
cp config.toml.example ~/.config/freellmpool/config.toml
# merge [keys] from .env into config or export env vars
```

FreePool defaults **`routing = "spread"`** to spread load across providers (diversity).

### 3. Run proxy

**Docker:**

```bash
cp .env.example .env
docker compose up -d
curl -sS -H "Authorization: Bearer ${FREEPOOL_PROXY_KEY:-dev-local-key}" http://127.0.0.1:8080/v1/models | head
```

**Host CLI:**

```bash
export FREELLMPOOL_ROUTING=spread
freellmpool proxy --host 127.0.0.1 --port 8080
```

### 4. Wire Hermes

```bash
freellmpool profile show hermes
freellmpool profile hermes --doctor
```

Point Hermes custom OpenAI endpoint at `http://127.0.0.1:8080/v1`, model family `quality` or rely on proxy `spread` default; use any placeholder API key string unless the proxy requires a bearer token (`proxy_key` / `FREEPOOL_PROXY_KEY`).

### 5. Tailscale (optional, recommended for remote agents)

```bash
freellmpool tailnet serve --port 8080
```

Use the generated session bearer on remote clients; do not expose plain HTTP on the public internet.

## Routing cheat sheet

| Mode | Use when |
|------|----------|
| `spread` | **Default for FreePool** — maximize provider diversity |
| `agent` | Long tool-heavy loops on strongest free capability tier |
| `quality` | Hard prompts; capability + latency scoring |
| `fast` | Lowest latency free route |
| `fair` | Even provider rotation |

Set via `config.toml` `[settings] routing` or `FREELLMPOOL_ROUTING`.

## Verified tools only

Enable only routes that pass freellmpool conformance for your agent’s tool schema. Run health probes before relying on a route in production loops:

```bash
freellmpool providers health
```

## Paid providers

FreePool does **not** configure Codex/OpenAI paid profiles. If you add paid keys to freellmpool manually, keep them on separate profiles — never set pool default routing to paid upstreams.
