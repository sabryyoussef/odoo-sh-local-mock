# Batch 1 — UI-first skeleton

## Objective

Ship a runnable local web app that visually resembles Odoo.sh Branches/History, using dummy data only.

## File tree

```text
odoo-sh-local-mock/
├── docker-compose.yml
├── README.md
├── BATCH1_UI_PLAN.md
└── control-api/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── __init__.py
        ├── main.py
        ├── dummy_data.py
        ├── templates/
        │   ├── base.html
        │   ├── branches.html
        │   ├── placeholder.html
        │   └── partials/
        │       ├── top_nav.html
        │       ├── sidebar.html
        │       ├── branch_header.html
        │       ├── tabs.html
        │       ├── history_timeline.html
        │       └── event_card.html
        └── static/
            ├── css/app.css
            └── js/app.js
```

## Tasks

1. Compose + FastAPI container on `:8000`
2. Dummy project `alzaeem` with Production/Staging/Development branches
3. Reusable Jinja partials matching Odoo.sh layout
4. HISTORY timeline cards; other tabs as placeholders
5. Lightweight JS: branch filter, copy clone command, tab placeholders

## Out of scope

Git, Docker builds, Odoo runtime, Postgres build DBs, webhooks, auth, SQLite (not needed yet).
