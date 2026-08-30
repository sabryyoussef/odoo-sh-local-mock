# Mock Odoo.sh

Local UI/UX mock of an Odoo.sh-style cloud platform for demos and portfolio use.

**Live demo:** https://mock-odoo.drpaws.ai/  
**Full walkthrough + screenshots:** [docs/DEMO.md](docs/DEMO.md)

## Quick start

```bash
docker compose up -d --build
```

Open http://localhost:8000/

## Demo flow (short)

1. Deploy Your Platform → GitHub login → Authorize  
2. Choose Professional → Checkout → Pay (demo)  
3. Deploy with code `MOSH-2026-ABCD-1234`  
4. Open Project → Branches → Build #21 → Logs / Connect  

## Stack

FastAPI · Jinja2 · plain CSS · Docker Compose · Playwright screenshots

## Docs

| Doc | Description |
|---|---|
| [docs/DEMO.md](docs/DEMO.md) | Recruiter walkthrough with Playwright screenshots |
| [docs/screenshots/](docs/screenshots/) | PNG captures of every main screen |

Recapture screenshots:

```bash
npm install && npx playwright install chromium
BASE_URL=http://localhost:8000 npm run screenshots
```

## Disclaimer

Mock only — no real OAuth, payments, Git, or Odoo runtime.
