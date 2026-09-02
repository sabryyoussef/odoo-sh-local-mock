# Helpers ERP Cloud onboarding UI — test evidence

**Stamp:** `2026-09-02T1248`  
**Artifact root (gitignored, local):**  
`/opt/projects/active/odoo-sh-local-mock/control-api/e2e/artifacts/cloud-onboarding-evidence/2026-09-02T1248/`  
**Inspected:** every named PNG below was opened and reviewed; selector-only pass was not treated as visual proof.  
**Inference:** OmniRoute `cursor-free` (`muse-spark-1.2-contributor-free`)

Primary Playwright traces/videos were copied out of `test-results/` (Playwright wipes that directory on later runs):

| Journey | Trace | Video |
|---------|-------|-------|
| A desktop Chromium | `traces/journey-A-desktop-chromium/trace.zip` | `traces/journey-A-desktop-chromium/video.webm` |
| A mobile Chromium 390×844 | `traces/journey-A-mobile-chromium/trace.zip` | `traces/journey-A-mobile-chromium/video.webm` |
| A desktop Firefox | `traces/journey-A-desktop-firefox/trace.zip` | `traces/journey-A-desktop-firefox/video.webm` |

Machine-readable Cloud-journey results from the inspected run: `playwright-results.json` in the same folder.  
Full-suite Playwright JSON (later `npm run test:e2e`): `full-suite-playwright-results.json`.

Unless noted, backend tests are in `control-api/tests/test_cloud_onboarding_ui.py` and `test_helpers_erp_cloud.py`.

## Journey A — new Business, monthly

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Homepage product sign-in | Journey A @cloud-primary | `test_contextual_sign_in_targets` | PASS | `01-homepage-product-signin.png`, `-mobile`, `-firefox` | Cloud sign in `/cloud/login` and Developer sign in `/login` present. Landing branding (unrelated dirty work) is visible; not part of Cloud commits. |
| Cloud overview + contextual Sign in | Journey A | `test_product_positioning_not_mixed_on_cloud_overview` | PASS | `02-cloud-overview.png` (+ mobile/firefox) | “View plans and start”; Sign in → `/cloud/login`; isolation copy; no GitHub. |
| Monthly pricing | Journey A | catalog seed / pricing tests | PASS | `03-cloud-pricing-monthly.png` | Trial CTA **Start free**; Business **Choose Business**; Enterprise **Custom quote**; annual disclaimer present. |
| Plan/cycle preserved through register | Journey A | post-auth destination tests | PASS | `05-cloud-register.png` | URL carries `plan=business` & `cycle=monthly`. |
| Short registration + auto login | Journey A | register tests | PASS | `05`, `07` | Lands on `/cloud/setup`; no version step; no `Select Odoo version`. |
| Configure package/company/Egypt/ar_001 | Journey A | country/language tests | PASS | `07-cloud-configure-default.png`, `08-cloud-configure-customized.png` | Egypt selected; language العربية; users 8; storage 15; Egyptian add-on checked. |
| Authoritative quote refresh | Journey A | `test_quote_preview_uses_4xx_for_invalid_requests` | PASS | `09-cloud-server-quote.png` | After **Update total**: **$189.00 / month** = Business $149 + Trading $20 + add-ons $20. |
| Confirm line items + hostname | Journey A | review snapshot tests | PASS | `10-cloud-confirm.png` (+ mobile/firefox) | Breadcrumb **Confirm**; hostname `*.helpers-erp.example`; `Egypt · EGP · ar_001`; CTA **Place demo order**. Locale code `ar_001` is slightly technical on the confirm row (stored value, as specified). |
| Client-posted fake total ignored | Journey A (`total_cents=1`) | `test_confirm_idempotency_double_submit` | PASS | `10`, `11` | Success page does **not** show `$0.01`. |
| Double submit → one order | Journey A `orderCount==1` | `test_confirm_idempotency_double_submit` | PASS | `11-cloud-success.png` | Second POST replays success; **no** leftover “Finish configuring…” flash. Breadcrumb **Order received**. Status Queued. |
| Instances destination, Odoo 19 | Journey A | instance tests | PASS | `12-cloud-instances.png` | Odoo **19.0**; business/trading; 8/25 users; 15/50 GB; queued; no Open Odoo; no GitHub. |

## Journey B — annual Starter

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Annual catalog totals, no invented discount | Journey B | pricing service `annual_is_yearly_total` | PASS | `04-cloud-pricing-annual.png`, `B-annual-success.png` | Starter **$490.00 / year** (not $588 = 12×$49). Copy: yearly totals, not 12× monthly. |

## Journey C — Trial

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Start free, monthly force, paid add-ons removed | Journey C | `trial_forces_monthly`, addon compatibility | PASS | (pricing `03`; downgrade-to-trial `15`) | CTA **Start free**. Paid add-on checkboxes absent on Trial. |

## Journey D — Enterprise

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Custom quote, no fixed payable total | Journey D | quote_required plan tests | PASS | `13-cloud-enterprise-custom-quote.png`, `14-cloud-enterprise-confirm.png` | Sidebar **Custom quote** (empty of a payable total). Confirm CTA **Submit Enterprise request**. |

## Journey E — resume precedence

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| No draft → pricing; incomplete → setup; new plan → setup; confirm-ready → confirm; completed → instances; completed + new plan → new configure | Journey E | `post_auth_destination` / `classify_draft` | PASS | (no extra named shot; covered by A/E flow) | All six precedence paths asserted in UI. |

## Journey F — plan downgrade

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Users/storage capped; incompatible add-ons removed; company kept; quote recalculated | Journey F | `normalize_selection_for_plan` | PASS | `15-cloud-downgrade-normalized.png` | After Trial: company **Downgrade Co** kept; users 3 / storage 5; paid add-ons unavailable; quote **$20.00** Trading only. Egyptian add-on remains compatible with Starter; it is removed on Trial. |

## Journey G — validation and security UX

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Empty register | Journey G | register 400 | PASS | `06-cloud-register-validation.png` | Alert “Please correct the highlighted fields.”; field errors; red borders. |
| Mismatch / invalid email / aria-invalid / error-summary focus | Journey G | same | PASS | `06` | `#form-error-summary` focused; `aria-invalid=true`. |
| Invalid plan/cycle | Journey G | `parse_plan_cycle` | PASS | — | Lands on pricing or setup, not a crash. |
| Unsafe `next` | Journey G `/cloud/login?next=https://evil.example/steal` | allowlist (`CLOUD_ALLOWED_*`); login ignores unrestricted `next` | PASS | — | Final URL stays `/cloud/…`. |
| Reserved / short / long / invalid workspace | Journey G | `RESERVED_SUBDOMAINS` | PASS | `16-cloud-workspace-validation.png` | `admin` → “This workspace address is reserved.” |
| Arabic company fallback slug | Journey G | `fallback_workspace_slug` | PASS | — | `workspace-{id}.helpers-erp.example`. |
| Duplicate workspace + CSRF | Journey G2 | CSRF `validate_csrf` | PASS | — | Duplicate slug error; CSRF POST without token rejected. |
| Fake total + double-click | Journey A + confirm button `data-disable-after-submit` | DB unique `CloudOrder.idempotency_key` | PASS | `11` | Server idempotency, not only disabled button. |

## Journey H — product-line isolation

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Cloud `/cloud/login` vs GitHub `/login` | Journey H @cloud-primary; `three-product-lines` GitHub login | `test_cloud_registration_does_not_replace_github_login` | PASS | `17-developer-github-login.png` (+ mobile/firefox) | GitHub primary CTA; “Developer Platform only”; Cloud email path present. Cloud pages have no repo/upload/build controls. |

## Journey I — compatibility URLs

| Criterion | Playwright | Backend | Result | Screenshots | Visual notes |
|-----------|------------|---------|--------|-------------|--------------|
| Legacy wizard GETs, review/checkout → confirm, POST `/cloud/checkout` | Journey I | `test_legacy_route_redirects` | PASS | — | `/cloud/setup/version|package|company|addons` → `/cloud/setup`. Review/checkout GET → confirm when ready. |

## Responsive / a11y / no-JS

| Check | Command / test | Result | Notes |
|-------|----------------|--------|-------|
| Chromium desktop | `cloud-onboarding-chromium` 12 passed, retries 0 | PASS | Viewport 1280×720. Overflow assert ≤32px on homepage and instances. Sticky marketing nav can duplicate in **fullPage** screenshots; live layout uses `position:sticky` without overlapping form controls. |
| Chromium mobile ~390×844 | `cloud-onboarding-mobile` 2 passed, 1 skipped (keyboard) | PASS | Quote sidebar `order:-1` (total first). Nav links wrap. No horizontal overflow in Journey A. DPR makes PNG width ~1073px for 390 CSS px. |
| Firefox auth + primary | `cloud-onboarding-firefox` 3 passed; `desktop-firefox` auth 4 passed | PASS | Journey A, H, keyboard. |
| Keyboard-only | keyboard-only smoke @cloud-primary | PASS | Skipped on mobile by design (not a silent exclude of a required desktop check). |
| No-JS fallback | “Journey A works with JavaScript disabled” | PASS | `Show prices` noscript; **Update total** server re-render. |
| Contrast tooling | — | not run | No Cloud-specific contrast scanner in-repo. `a11y.spec.js` covers Quick Deploy, not Cloud. |

## Regression commands (pre-commit)

| Suite | Command | Result |
|-------|---------|--------|
| Focused Cloud + product-line | `docker compose exec -T control-api python -m pytest tests/test_cloud_onboarding_ui.py tests/test_helpers_erp_cloud.py tests/test_product_line_regression.py tests/test_three_product_navigation.py tests/test_e2e_harness.py -q` | **76 passed** in 50.30s |
| Full non-integration backend | `docker compose exec -T control-api python -m pytest tests/ -m "not integration" -q` | **296 passed, 1 skipped, 2 deselected** in 160.80s |
| Dedicated Cloud journeys | `CLOUD_EVIDENCE_STAMP=2026-09-02T1248 npx playwright test --config=control-api/e2e/playwright.config.js --project=cloud-onboarding-chromium --project=cloud-onboarding-mobile --project=cloud-onboarding-firefox` | **17 passed, 1 skipped, 0 retries** in 1.4m |
| Complete Playwright | `npm run test:e2e` | **49 passed, 1 skipped, 0 flaky, 0 retries** in 2.3m (137.4s JSON duration) |

Skipped tests: mobile keyboard-only (`test.skip` when project name includes `mobile`). Not retried-until-green.

## Screenshot inventory (desktop required set)

All under `…/2026-09-02T1248/`:

`01-homepage-product-signin.png`  
`02-cloud-overview.png`  
`03-cloud-pricing-monthly.png`  
`04-cloud-pricing-annual.png`  
`05-cloud-register.png`  
`06-cloud-register-validation.png`  
`07-cloud-configure-default.png`  
`08-cloud-configure-customized.png`  
`09-cloud-server-quote.png`  
`10-cloud-confirm.png`  
`11-cloud-success.png`  
`12-cloud-instances.png`  
`13-cloud-enterprise-custom-quote.png`  
`14-cloud-enterprise-confirm.png`  
`15-cloud-downgrade-normalized.png`  
`16-cloud-workspace-validation.png`  
`17-developer-github-login.png`  
plus `B-annual-success.png` and `*-mobile.png` / `*-firefox.png` counterparts for the primary set.
