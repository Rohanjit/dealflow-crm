# Dealflow

**An open-source, self-hosted CRM built for investment bankers.**

Dealflow tracks the full life of a mandate, from the first approach to a company through to closing, alongside the investor relationships that make deals happen. It runs on your own laptop or server, so confidential deal data never leaves your firm.

> **Status:** in development. V1 is being built module by module. Foundation, Investors, Contacts, Outreach and Active Deals are done.

## What's in V1

| Module | What it does |
| --- | --- |
| **Investors** | Investor firms with preferences (sectors, stages, ticket size, geography), fund profile, relationship owner and linked contacts. |
| **Contacts** | People at investor firms and at client companies, in one place. |
| **Outreach** | Origination pipeline for winning mandates: Identified → Contacted → Meeting held → Proposal sent → Negotiating → Mandate won, on a drag-and-drop board. Each company is recorded once, with every approach, its people and an interaction log. |
| **Active Deals** | Execution pipeline from data collection to closing (a mandate won in Outreach becomes a deal automatically), with the deal team, fees, a per-deal investor tracker with funnel counts, rules-based suggested investors, and a home-page snapshot of deals near their target close and mandates nearing expiry. |
| **Tasks & Reminders** | Follow-ups assigned to people, linked to any record, with in-app reminders. |
| **Vault** | Confidential documents stored on your own server, protected by a separate Vault PIN. |

Built for firms, not individuals:

- **Role-based access.** Admins create roles and decide what each role can see and do in each module.
- **Field switches.** Turn any field on or off to match how your firm works.
- **Editable dropdowns.** Sectors, investor types, reasons and categories are yours to change.
- **Multi-currency.** Every amount carries its own currency and unit (₹ Cr, $ Mn, and so on).
- **Import and export.** Bring in existing Excel sheets with column matching and duplicate warnings; export any list to Excel.
- **Archive, not delete.** Removed records go to an archive admins can restore from.
- **Daily backups.** Automatic backups on your server, plus one-click download and restore.

## Self-hosted by design

Dealflow is not a hosted service. You run it yourself with Docker, on a laptop for a small team or on your firm's own server. SQLite is the default database, with Postgres available for larger teams.

## Install

You need [Docker](https://docs.docker.com/get-docker/) with Compose.

```sh
git clone https://github.com/Rohanjit/dealflow-crm.git
cd dealflow-crm
docker compose up -d
```

Open <http://localhost:8000>. The setup wizard asks for your firm's name and creates the first Admin account. Then add your team under **Settings → Users** (create accounts directly, or share a one-time invite link).

All data lives in the `dealflow-data` Docker volume: the database, uploaded files and daily backups (in `/data/backups` inside the volume). Backups can also be downloaded and restored from **Settings → Backups**.

Optional settings go in a `.env` file next to `docker-compose.yml`; see [`.env.example`](.env.example) for the port, time zone, Postgres and HTTPS options.

**Postgres instead of SQLite:** set `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`, then run `docker compose --profile postgres up -d`.

**Updating:** `git pull && docker compose up -d --build`. Database changes are applied automatically on start.

## Development

```sh
# API on :8000
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DJANGO_DEBUG=1 python manage.py migrate && DJANGO_DEBUG=1 python manage.py runserver

# App on :5173 (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

Tests: `cd backend && python manage.py test` and `cd frontend && npm test`.

## Tech stack

- Backend: Python, Django, Django REST Framework
- Frontend: React, TypeScript
- Packaging: Docker Compose

## License

[MIT](LICENSE)
