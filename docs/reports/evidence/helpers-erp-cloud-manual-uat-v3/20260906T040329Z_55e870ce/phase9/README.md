# Phase 9 — Real Browser Playwright PNG Evidence

Run ID: 20260906T040329Z_55e870ce
Date: 2026-09-06T09:44Z (recaptured on http://100.76.217.35:8001, not localhost)
Tool: Playwright chromium, headless, --no-sandbox, BASE=http://100.76.217.35:8001

## Coverage
- Pricing monthly/annual desktop: 01,02
- Login desktop + AR: 03,04
- Cloud overview: 05
- Per-user (user1-4) instances ready desktop: 10-*
- Per-user setup: 11-*
- Per-user pricing: 12-*
- Per-user instance detail (shows Tailscale URL + Open Odoo): 13-*
- Per-user setup review/confirm: 14-*,15-*
- Mobile pricing + login + user1 instances: 20,21,22
- Odoo login per port 8301-8304: 30-*
- Odoo home (logged in as user1-4, /odoo/discuss): 31-*

## Verification
- All 36 PNGs are valid PNG image data, 1280x800 or Pixel 5
- No secrets in screenshots (password 123 not visible, PG passwords not present)
- Tailscale URLs visible: http://100.76.217.35:8301-8304 and http://master.tailcf9988.ts.net:8301-8304
- Open Odoo links present in instances pages
- Odoo login works for user1-4/123, home shows Discuss/Apps
- Desktop + mobile covered
- Arabic login captured

## Files
36 PNGs + this README
