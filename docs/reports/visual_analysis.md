# Visual analysis — UAT V3 Playwright (RUN `20260906T111621Z_ef1bb46c`)

Every retained PNG was opened as an image. Verdicts below are from pixels, not HTTP/DOM inference.

**Retained:** 36 PNGs (desktop 1440×1000 × 7 per user × 4 users = 28; Pixel 5 × 2 per user × 4 = 8).  
**Discarded (exact paths):** `user1-99-error.png` … `user4-99-error.png` from the first Odoo login-click miss (hidden “Skip to Content”). Not part of the approval set.

## Method

- Desktop Chromium viewport 1440×1000 against `http://100.76.217.35:8001` and `http://100.76.217.35:8301–8304`.
- Pixel 5 (`devices['Pixel 5']`, 393×727) against the portal only.
- Pixel 5 instance shots were recaptured after the form-submit selector fix; the retained `*-pixel5-02-portal-instance.png` files are the rerun.

## Per-user visual result

| User | Portal login | Portal instance | Odoo login | Odoo home | Company | Apps | Functional menu | Pixel 5 |
|------|--------------|-----------------|------------|-----------|---------|------|-----------------|---------|
| user1 | PASS | trial / sales / Ready | PASS (logo placeholder) | PASS (Discuss) | User 1 Demo Company | CRM, Sales, Invoicing | CRM pipeline | PASS |
| user2 | PASS | starter / trading / Ready | PASS (logo placeholder) | PASS (Discuss) | User 2 Demo Company | +Purchase, Inventory | Inventory Overview | PASS |
| user3 | PASS | business / operations / Ready | PASS (logo placeholder) | PASS (Discuss) | User 3 Demo Company | Purchase, Inventory, Manufacturing, Maintenance, Employees; no CRM/Sales | Inventory + Manufacturing card | PASS |
| user4 | PASS | enterprise / full_erp / Ready | PASS (logo placeholder) | PASS (Discuss) | User 4 Demo Company | full set including Project | Project empty state | PASS |

## Isolation (visual)

Each portal instance page shows **one** workspace card. No other `User N Demo Company` appears. Odoo headers show only that user’s company. Apps menus match the assigned package (user3 has no CRM/Sales; user1 has no Inventory/Purchase).

## Findings that are not white screens

1. **Odoo login “Your logo” placeholder** (all four tenants). Default Odoo 19 login card; no custom company logo uploaded. Layout otherwise clean.
2. **Duplicate navbar “Discuss Discuss”** on Odoo home (all four). Odoo 19 default Discuss app label duplication. Not clipping or overlap of other chrome.
3. **user1 CRM empty overlay:** “As you are a member of no Sales Team…”. Expected empty demo; pipeline columns remain visible behind the overlay.
4. **user4 Project empty state:** “No projects found. Let's create one!” — expected empty demo.
5. **Pixel 5 instance card:** plan/package/Ready/company visible; Open Odoo and lower quota rows sit **below the fold**. Scroll required. Not overlap or broken CSS.
6. **Portal EN | عربي** language toggle is visible and LTR English is consistent. No mixed RTL layout breakage on EN. Arabic was not switched in this run.
7. No missing portal icons, no stuck spinners, no untranslated `i18n` keys, no localhost strings in screenshots.

## White screens / broken assets / overlap

None on retained PNGs. `/odoo` loads Discuss with OdooBot, sidebar, and navbar. Portal login and instance cards render with gradient, logo, and buttons.

## Localhost

No `localhost` / `127.0.0.1` Open Odoo targets in screenshots or recorded hrefs. All Open Odoo links are `http://100.76.217.35:830N/web/login?db=helpers_demo_userN`.
