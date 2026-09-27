# Dealflow

**An open-source, self-hosted CRM built for investment bankers.**

Dealflow tracks the full life of a mandate, from the first approach to a company through to closing, alongside the investor relationships that make deals happen. It runs on your own laptop or server, so confidential deal data never leaves your firm.

> **Status:** in development. V1 is being built module by module. This README will carry full install instructions at release.

## What's in V1

| Module | What it does |
| --- | --- |
| **Investors** | Investor firms with preferences (sectors, stages, ticket size, geography), fund profile, relationship owner and linked contacts. |
| **Contacts** | People at investor firms and at client companies, in one place. |
| **Outreach** | Origination pipeline for winning mandates: Identified → Contacted → Meeting held → Proposal sent → Negotiating → Mandate won. |
| **Active Deals** | Execution pipeline from data collection to closing, with a per-deal investor tracker and rules-based investor matching. |
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

## Tech stack

- Backend: Python, Django, Django REST Framework
- Frontend: React, TypeScript
- Packaging: Docker Compose

## License

[MIT](LICENSE)
