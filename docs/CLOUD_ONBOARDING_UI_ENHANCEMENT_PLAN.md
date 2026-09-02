# Helpers ERP Cloud — onboarding UI enhancement plan

**Date:** 2026-09-02  
**Status:** Implemented 2026-09-02 (see chat report `HELPERS_ERP_CLOUD_ONBOARDING_UI_ENHANCEMENT_REPORT`)  
**Product line:** `helpers_cloud` only  
**Journey (this pass, not reduced further):**

`/cloud` → `/cloud/pricing` → Cloud auth → `/cloud/setup` → `/cloud/setup/confirm` → Success / Instances

No auth modals. No schema migration unless a blocker appears (none expected).

## Baseline (Phase 0)

Recorded at start of this work:

- Branch: `main`
- HEAD: `2b333e4246e5fdb0b2af518757fe737c1069adcf` (`[DOC] platform: document DP6 controlled activation`)
- Ahead of `origin/main` by 23 commits
- Working tree: unrelated branding/i18n/landing work already dirty — **preserve; do not revert**
- Cloud routes today: overview, pricing, register, login, six-step wizard, review, checkout, instances
- Inference for this implementation: OmniRoute `cursor-free` (`muse-spark-1.2-contributor-free`)

## Authoritative billing rules (catalog)

Seeded in `cloud_catalog_service._seed_plans` / packages / add-ons. Server formula in `calculate_cloud_price`:

```
total = plan_base
      + extra_users * per_user
      + extra_storage_gb * per_gb
      + package_adjustment
      + add-ons
      - discount
      + tax
```

- `price_monthly_cents` is the **monthly** plan base.
- `price_annual_cents` is the **annual total** (not monthly-equivalent). Example: Starter monthly `$49.00`, annual `$490.00` (~10× monthly, i.e. catalog already encodes ~2 months free). UI must label annual as **/ year** and may show monthly-equivalent as `annual/12` for illustration only. **Do not invent an extra discount.**
- Add-on and package annual cents follow the same annual-total convention.
- Trial (`code=trial`, `is_demo=True`, both bases 0): billing cycle is always `monthly`. Annual is meaningless and must be normalized away.
- Enterprise (`quote_required=True`): not a fixed-price checkout. Show **Custom quote**. CTA **Submit Enterprise request**. Demo may still create the existing demo order/provisioning records.
- Client-submitted totals are ignored. Recalculate on configure save, confirm GET, and confirm POST.

`plan` / `cycle` query values: accept known plan codes and `monthly`/`annual` only; unknown values are dropped (safe normalize), never applied.

## Session intent

Session key: `cloud_plan_intent` = `{plan: str, cycle: str}`.

Set when a pricing CTA is followed (`/cloud/register?plan=&cycle=` or `/cloud/setup?plan=&cycle=`). Persist across register/login. Consume when applied to a draft. Logout clears session (existing `logout_user`).

Redirect allowlist (no unrestricted `next`):

- Exact: `/cloud`, `/cloud/pricing`, `/cloud/register`, `/cloud/login`, `/cloud/setup`, `/cloud/setup/confirm`, `/cloud/instances`, `/cloud/checkout/success`
- Prefix: `/cloud/instances/`, `/cloud/subscriptions/`, `/cloud/provisioning/`, `/cloud/checkout/success`

Unsafe `next` is ignored; resume precedence decides.

## Login / resume precedence

Do **not** create a draft on login.

1. Valid `plan`/`cycle` intent → `apply_plan_from_code` + auto Odoo 19 → `/cloud/setup`
2. Incomplete draft (has `plan_id`, missing company/package) → `/cloud/setup`
3. Confirm-ready draft → `/cloud/setup/confirm`
4. No usable draft and no instances → `/cloud/pricing`
5. Completed instance/order and no new intent → `/cloud/instances`

Never send a new/incomplete buyer to an empty Instances page.

## Plan normalization

`normalize_selection_for_plan(db, setup, plan) -> setup`

When plan changes:

- Cap users at `plan.max_users` (if set)
- Cap storage at `plan.max_storage_gb` (if set)
- If users/storage missing, set to plan included amounts
- Auto-assign recommended supported version `19.0`
- Auto-assign the recommended compatible package when none is saved (so add-ons are not all disabled on first configure)
- Force Trial to `monthly`
- Drop add-ons incompatible with package/version, failing dependencies, or paid add-ons on Trial/demo plans
- Recalculate quote when enough fields exist
- Keep valid company/package
- Never leave users/storage above plan maxima

## Subdomain

Reuse `slugify` from `project_service` with Cloud fallback `workspace-{user_id}` when the slug is empty (Arabic / non-Latin names).

Validate on **submit** only (not while typing): uniqueness vs other non-abandoned setups and instances; reserved set including `admin`, `api`, `www`, `cloud`, `platform`; length 3–48; `[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?`. Field-level errors. Display `{slug}.helpers-erp.example`. Hostname is reserved/finalized at existing demo order/provisioning, not during typing.

## Country / language

| Country | Currency | Timezone | Extra |
|---------|----------|----------|--------|
| Egypt | EGP | Africa/Cairo | locked |
| Saudi Arabia | SAR | Asia/Riyadh | locked |
| UAE | AED | Asia/Dubai | locked |
| Other | default USD / UTC | **editable** currency + timezone |

Language: English → `en_US`; العربية → `ar_001`.

## Contextual Sign In

- `/cloud` and `/cloud/*`: Sign in → `/cloud/login`
- `/platform` and Developer Platform pages: Sign in → `/login` (GitHub)
- Homepage (and other neutral marketing such as `/pricing`): **Cloud sign in** + **Developer sign in**
- GitHub `/login` keeps copy directing company buyers to Cloud login

## UI stages

1. Contextual nav + `/cloud` one primary CTA `View plans and start`, secondary `Sign in`
2. Pricing: cycle toggle; CTAs Start free / Choose Starter / Choose Business / Continue with Enterprise; carry plan+cycle
3. Short register (name, email, password, confirm, terms) + login/resume
4. Combined `/cloud/setup` + server quote
5. Confirm, Enterprise custom quote, idempotency
6. Compatibility redirects + legacy drafts
7. Responsive / a11y polish
8. Full regression

### Configure page fields

Primary: package, legal company name, workspace name, generated workspace URL, country, language.  
Secondary (`Customize your plan`, accessible without JS): users, storage, optional add-ons.  
Copy: `Choose how you work`, `Your company workspace`, `Customize your plan`. No “you cannot upload modules”.

### Confirm

Merge review + checkout. Idempotency token (session + hidden field). Disable CTA after submit. Recalculate on POST. Ignore posted totals. Double-submit returns the same order.

## Compatibility redirects

| Old | New |
|-----|-----|
| GET `/cloud/setup/plan` | `/cloud/pricing` or `/cloud/setup` if plan saved |
| POST `/cloud/setup/plan` | save plan (legacy) then `/cloud/setup` |
| `/cloud/setup/version\|package\|company\|addons` | `/cloud/setup` |
| `/cloud/setup/review` | `/cloud/setup/confirm` |
| GET `/cloud/checkout` | `/cloud/setup/confirm` |
| POST `/cloud/checkout` | same handler as confirm POST |

## Security invariants (unchanged)

PBKDF2; CSRF; rate limits; Cloud `next` allowlist; server prices; product-line isolation; no GitHub/repos/uploads on Cloud; demo checkout without PAN/CVV; mocked provisioning.

## Out of scope

Real payment, email verification, password reset, live Odoo, Ready Solutions / Developer Platform wizards (except contextual nav/login routing).

## Risks

- Dirty branding/i18n tree: edit only Cloud onboarding + required nav/login/translations/tests
- `get_or_create_draft_setup` must not run on login (would steal Instances)
- Playwright e2e uses isolated Compose; do not point at live `control.db`
- Existing tests assert old copy/routes — update Cloud tests only

## Test gates

After each stage: backend tests for the changed surface; HTML assertions; Playwright for the Cloud journey when templates exist; screenshots desktop + mobile + one error state. Full pytest Cloud + product-line + Playwright product-lines before PASS.

## Rollback

Restore Cloud files from `2b333e4` if needed. Do not restore unrelated dirty branding files. Database schema is unchanged; abandon in-progress Cloud drafts via `status=abandoned` if a demo DB was used.
