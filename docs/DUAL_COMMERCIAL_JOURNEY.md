# Dual Commercial Journey — Decision Record

**Date:** 2026-09-01  
**Task:** `ODOO_SAAS_DUAL_COMMERCIAL_JOURNEY_REFACTOR`

## Decision

Solution-first customers purchase **one Solution Package** with included infrastructure (hosting, backups, users, storage, environments).

Developer-first customers purchase a **Platform Plan** for their own Git-backed projects.

These are **independent journeys**; neither is a prerequisite for the other.

## Data model

| Journey | Table | Type field | Links |
|---------|-------|------------|-------|
| Business Solution | `customer_subscriptions` | `subscription_type = solution` | Solution → Package → Tenant → Environments |
| Developer Platform | `subscriptions` | `subscription_type = platform` | PlatformPlan → Projects → Branches → Builds |

`platform_plans` seeds Trial / Developer / Professional from presentation data.

Integrity enforced in `app/services/subscription_integrity.py` and at creation time in catalog/portal services.

## Routes

| Path | Purpose |
|------|---------|
| `/pricing` | Hub — choose Business Solutions vs Developer Platform |
| `/solutions` | Alias → `/catalog` |
| `/platform` | Developer Platform landing |
| `/platform/pricing` | Trial / Developer / Professional plans |
| `/checkout` | Platform plan checkout (unchanged) |

## Terminology (customer-facing)

| Context | Term |
|---------|------|
| Business product | Solution |
| Commercial level inside Solution | Solution Package |
| Developer hosting level | Platform Plan |
| Customer contract | Subscription |
| Persistent Solution installation | Tenant |
| Git-triggered artifact | Build |
| Production/Staging/Development runtime | Environment |

## Migration

- Existing `customer_subscriptions` → classified as `solution`
- Existing `subscriptions` → classified as `platform`, linked to `platform_plans` by plan name
- Ambiguous records reported via `classify_legacy_subscriptions()` — none guessed
