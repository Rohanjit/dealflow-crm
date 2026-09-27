# Dealflow — project brief for Claude

Read this first in every session. The full product scope is in `docs/scope-v1.md`; this file covers how we work and how the code is organised.

## Working model

- Shobhit (product owner, ex-investment banker) makes product decisions and answers questions. He does not write code.
- Claude writes all code, tests it, and pushes to this repo.
- Ask product questions **one at a time**. Technical choices are Claude's to make, but record them in this file.
- The repo stays **private** until all six V1 modules are done.
- Build order (finish each before the next): Foundation + Investors → Contacts → Outreach → Active Deals → Tasks & Reminders → Vault.
- Anything outside V1 scope goes to the "Not in V1" list in `docs/scope-v1.md`. Don't build it without a decision.

## Stack

- `backend/`: Python 3.11+, Django 5.1, Django REST Framework, django-filter, openpyxl. Served by gunicorn; the built frontend is served by Django through whitenoise, so the app ships as **one container**.
- `frontend/`: React + TypeScript, built with Vite. Mantine for UI components, TanStack Query for data fetching, React Router.
- Database: SQLite by default (file in the data volume); Postgres when `DATABASE_URL` is set.
- `docker-compose.yml`: `app` service, `scheduler` service (same image, runs daily backups), optional `db` (Postgres) behind a compose profile.
- Auth: Django session cookies + CSRF (the SPA and the API share one origin). No tokens in localStorage.

## Architecture decisions

1. **Field registry drives the UI.** Each module declares its fields once in the backend (`<app>/fields.py`: key, label, group, type, dropdown list, required). The API serves this schema merged with the firm's on/off switches, and the frontend renders list columns, detail pages, forms, filters, import mapping and export columns from it. New modules reuse the same generic screens.
2. **Field types:** text, longtext, url, email, phone, integer, decimal, percent, date, boolean, tri-state (yes/no/unknown), fixed choice, dropdown (single/multi, from editable lists), user, money, tags.
3. **Money** is stored as three columns: amount (decimal), currency (ISO code) and unit (e.g. `Cr`, `Lakh`, `Mn`, `Bn`, `K`, or none). No conversion.
4. **Editable dropdowns** are `ChoiceList` + `ChoiceOption` rows. Records point at option IDs, so renames propagate. "Removing" an option deactivates it: existing records keep it, new records can't pick it.
5. **Permissions** are per role, per module: view / create / edit / delete, plus global flags: manage users, manage settings, restore archive. The built-in Admin role has everything and can't be edited or deleted.
6. **Archive:** every main record has `archived_at` / `archived_by`. API querysets hide archived rows. Archiving needs the module's delete permission; restoring needs the "Restore from archive" flag; permanent deletion is admin-only.
7. **Stamps:** every main record has `created_by/at` and `updated_by/at`, set automatically by the API.
8. **Per-user preferences** (date format, number grouping) live on the user and are applied only when formatting in the frontend. The API always sends ISO dates and plain numbers.
9. **Backups** are a zip of a Django `dumpdata` export plus uploaded files, so they restore on either SQLite or Postgres.
10. **Records API** is generic: `/api/m/<module>/` (list, create), `/api/m/<module>/<id>/` (read, PATCH, DELETE = archive), plus `export/` and `import/upload|check|commit/`. Registering a module (`<app>/fields.py` → `core.registry.register`) and adding it to `core.modules.BUILT_MODULES` is all a new module needs for the standard screens; the frontend renders from `/api/schema/`.
11. **`Record.objects` returns archived rows too**, so backups (`dumpdata`) include them. API querysets filter `archived_at__isnull=True` explicitly.
12. **Money** also keeps a hidden `<key>_value` column (amount × unit multiplier) for sorting and range filters. Comparisons only happen within one currency; sorting groups by currency first.
13. **Who can do what, beyond module permissions:** restoring from the Archive needs the "Restore from archive" flag; permanent deletion, role editing and backups (list, download, restore, schedule) are Admin-only. Users with "Manage users" can't create, edit or promote Admin accounts. At least one active Admin must always remain.
14. **Invites** are one-time links valid 7 days. Temporary passwords (admin-created accounts and resets) force a password change at next sign-in and end the person's other sessions.
15. **Import** keeps the parsed file under `DATA_DIR/imports/<token>.json` between steps (24 h). Duplicate warnings compare normalised firm names (generic words like "Ventures", "Capital" ignored) and website domains. Unknown dropdown values are left blank with a warning, unless someone with "Manage settings" ticks "add missing values". Slashed dates are read in the importing user's date format. Money cells understand "₹ 50 Cr", "$5mn", and a currency/unit taken from the column header.
16. **Export** writes one numeric column per money field plus a "– currency & unit" column; import recognises that pair, so exports round-trip.
17. **Starter data:** migrations seed the Admin role, an editable "Member" role (view/create/edit everywhere, no delete), and default dropdown lists for the modules that exist. Each later module seeds its own lists in its own migration.
18. **Scheduler** (`manage.py run_scheduler`) checks every minute and takes the daily backup at the hour set in Settings (default 02:00 server time), keeping the newest N of each kind (daily / manual / pre-restore). A restore takes a "pre-restore" safety backup first and signs everyone out.
19. Column choices on list pages are the only thing kept in browser storage (per user, per browser).

## Conventions

- Backend tests: `cd backend && python manage.py test`. Frontend: `cd frontend && npm run build` (type-checks) and `npm test`.
- Keep migrations committed. One Django app per module.
- Commit in small, working steps with clear messages.

## Status

- [x] Discovery and V1 scope (27 Sep 2026)
- [x] Repo created (private), license, README, this brief
- [x] Foundation + Investors (27 Sep 2026): setup wizard, login, users and invites, roles and permissions, field switches, editable dropdowns, record stamps, archive, global and per-module search and filters, import/export, settings, backups with daily scheduler, Docker Compose (SQLite and Postgres both tested). Home dashboard waits for Active Deals; Investors' "Linked contacts" and "Deal history" sections fill in with Contacts and Active Deals.
- [ ] Contacts
- [ ] Outreach
- [ ] Active Deals
- [ ] Tasks & Reminders
- [ ] Vault
