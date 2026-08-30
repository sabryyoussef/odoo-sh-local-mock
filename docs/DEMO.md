# Mock Odoo.sh — Demo Documentation

Local UI/UX mock of an Odoo.sh-style platform for development and demonstration.

**Live demo:** https://mock-odoo.drpaws.ai/

> This is a **mock only**. No real GitHub OAuth, payments, Git clones, or Odoo containers.

---

## Quick recruiter walkthrough

1. Open the live demo (or run locally — see below).
2. Click **Deploy Your Platform**.
3. Continue with GitHub → Authorize.
4. Choose **Professional** → Checkout → **Pay $49** (demo, no charge).
5. Copy subscription code → **Deploy Your First Project**.
6. Validate → Deploy → Open Project.
7. Explore Branches, Build #21, Logs, Connect, Backups, Settings, Account.

---

## End-to-end workflow

```text
Landing
  → Login (GitHub mock)
  → Authorize
  → Pricing
  → Checkout
  → Payment Success
  → Deploy Wizard
  → Deploy Progress
  → Projects / Branches
  → Build Details / Logs / Connect
  → Backups / Settings / Account
```

---

## Screenshots

Captured with Playwright (`npm run screenshots`).

### 1. Landing

![Landing](screenshots/01-landing.png)

### 2. GitHub login (mock)

![Login](screenshots/02-login.png)

### 3. GitHub authorization (mock)

![Authorize](screenshots/03-authorize.png)

### 4. Choose plan

![Pricing](screenshots/04-pricing.png)

### 5. Checkout

![Checkout](screenshots/05-checkout.png)

### 6. Payment success

![Payment success](screenshots/06-payment-success.png)

### 7. Deploy wizard

![Deploy](screenshots/07-deploy.png)

### 8. Deployment progress

![Deploy progress](screenshots/08-deploy-progress.png)

### 9. Projects dashboard

![Projects](screenshots/09-projects.png)

### 10. Branches

![Branches](screenshots/10-branches.png)

### 11. Build details

![Build details](screenshots/11-build-details.png)

### 12. Build logs

![Build logs](screenshots/12-build-logs.png)

### 13. Connect (mock Odoo)

![Connect](screenshots/13-build-connect.png)

### 14. Backups

![Backups](screenshots/14-backups.png)

### 15. Project settings

![Settings](screenshots/15-settings.png)

### 16. Account / subscription

![Account](screenshots/16-account.png)

---

## Run locally

```bash
cd odoo-sh-local-mock
docker compose up -d --build
```

Open: http://localhost:8000/

Stop: `docker compose down`

### Recapture screenshots

```bash
npm install
npx playwright install chromium
BASE_URL=http://localhost:8000 npm run screenshots
```

Screenshots are written to `docs/screenshots/`.

---

## Stack

- FastAPI + Jinja2 + plain CSS + minimal JS
- Docker Compose
- Dummy data only (no SQLite/Postgres app DB required for the mock)

---

## What is mocked (intentionally)

- GitHub OAuth / authorize
- Pricing & checkout / payments
- Subscription codes
- Deploy progress
- Branches / builds / logs / backups / connect

## Not implemented yet

- Real GitHub API, Git clone, webhooks
- Docker Odoo build engine
- Real PostgreSQL per build
- Real payment gateway (Paymob/Stripe)
- Auth sessions / security
