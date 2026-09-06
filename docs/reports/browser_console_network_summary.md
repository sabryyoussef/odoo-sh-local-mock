# Browser console / network summary — UAT V3 Playwright

RUN: `20260906T111621Z_ef1bb46c`  
Target: `http://100.76.217.35:8001` and tenant ports `8301–8304`.  
Logs are redacted (no cookies, CSRF, hashes, or database passwords).

## Desktop (users 1–4)

| User | Console errors | Page errors | Failed requests | HTTP ≥400 | URL transitions |
|------|----------------|-------------|-----------------|-----------|-----------------|
| user1 | none | none | none | none | `/cloud/login` → `/cloud/instances` |
| user2 | none | none | none | none | `/cloud/login` → `/cloud/instances` |
| user3 | none | none | none | none | `/cloud/login` → `/cloud/instances` |
| user4 | none | none | none | none | `/cloud/login` → `/cloud/instances` |

Desktop portal login used `form button.btn-hero[type="submit"]` after the first-run Odoo “Skip to Content” miss was fixed. No console noise on the successful desktop pass.

## Pixel 5 — first capture (wrong Sign in target)

All four first-run Pixel 5 sessions recorded:

- Console: `Failed to load resource: the server responded with a status of 400 (Bad Request)`
- HTTP: `400 POST http://100.76.217.35:8001/cloud/login`

Cause: `getByRole('button', { name: /Sign in/i })` hit the header **Sign in** link (or a non-submit control) instead of the form submit. Session was not created; `/cloud/instances` bounced back to login. Those instance PNGs were **replaced** by the rerun.

No pageerrors or requestfailed entries besides the 400 POST.

## Pixel 5 — rerun (retained instance PNGs)

Script: `playwright_v3_pixel5_rerun.js`  
Selector: `form button.btn-hero[type="submit"]` + wait until URL is not `/cloud/login`.

| User | urlAfter | company | Ready | isolationFail |
|------|----------|---------|-------|---------------|
| user1 | `http://100.76.217.35:8001/cloud/instances` | true | true | false |
| user2 | same | true | true | false |
| user3 | same | true | true | false |
| user4 | same | true | true | false |

Rerun exit 0. No additional console collectors on the rerun script (by design). Visual inspection of the four `*-pixel5-02-portal-instance.png` files confirms instances, not login.

## Localhost / mixed content

No recorded navigation to `localhost` or `127.0.0.1` for Open Odoo. Tenant logins used:

- `http://100.76.217.35:8301/web/login?db=helpers_demo_user1`
- `http://100.76.217.35:8302/web/login?db=helpers_demo_user2`
- `http://100.76.217.35:8303/web/login?db=helpers_demo_user3`
- `http://100.76.217.35:8304/web/login?db=helpers_demo_user4`

## Assets

No failed CSS/JS/image requests in desktop collectors. Odoo “Your logo” is an intentional placeholder graphic, not a 404 broken image overlay.
