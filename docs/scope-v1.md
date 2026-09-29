# Dealflow — V1 Scope

Open-source CRM for investment bankers. Discovery completed 27 Sep 2026.

## Project basics
- Name: Dealflow. Repo: `dealflow-crm`.
- License: MIT. Published on GitHub.
- Self-hosted only: runs locally or on the firm's own server. We do not host it.
- Installs with Docker Compose (one command).
- Multi-user from day one.
- Primary user for V1: investment bankers (VC workflow later).
- Timeline: flexible.

## Tech stack
- Frontend: React with TypeScript, using a ready-made component library (tables, forms, kanban).
- Backend: Python/Django as an API (Django REST Framework): login, role permissions, data, file uploads, search.
- Database: SQLite by default; Postgres optional via one Docker setting.

## Build and release plan
- Build module by module, finishing each before the next:
  1. Foundation (login, roles and permissions, field switches, editable dropdowns, archive, record stamps, search, import/export, settings, backups, setup wizard) alongside Investors
  2. Contacts
  3. Outreach
  4. Active Deals
  5. Tasks & Reminders
  6. Vault
- The GitHub repo stays private until all six modules are done; then V1 is published publicly.

---

## In V1

### First run
- Setup wizard only: create the Admin account, enter the firm name, start with an empty CRM. No demo data.

### Access control
- Role-based access at module level (admin sets what each role can view/edit per module).
- Admin can create custom roles.
- Admin decides visibility; the product does not impose it.
- Deleting is a separate permission: only roles granted "Delete" can remove records.

### User accounts
- Admin can add users either way: create the account directly (name, email, role, temporary password that must be changed at first login), or generate a one-time invite link to share by any channel (user sets own password; role pre-assigned).
- Admin resets forgotten passwords (no email in V1).

### Field switches
- Every field in every module can be switched on/off in Settings.
- Firm-wide setting, controlled by Admin and any role granted "Manage Settings".

### Editable dropdown lists
- Default lists ship for sectors, investor types, transaction types, lost/decline reasons, document categories, decision-maker levels, etc.
- Admin (or anyone with "Manage Settings") can add, rename or remove options.

### Record stamps
- Every record shows Created by / on and Last edited by / on.

### Deletion and archive
- Deleted records (investors, contacts, outreach, deals, tasks, Vault documents) go to an Archive.
- Admin can restore archived items or delete them permanently.

### Search
- Global search bar across all modules the user can access (investors, contacts, outreach, deals, tasks, Vault document titles).
- Per-module search and filters (e.g. sector, stage, owner, ticket size, tags).

### Home dashboard
- Active deals snapshot only: deals by stage, deals near target close date, mandates nearing expiry.
- Shown only to users whose role can access Active Deals.
- "Near" means within 30 days by default (overdue and expired ones included); firms change the window in Settings > Firm (default chosen 29 Sep 2026 while building).

### Data import and export
- Import from CSV/Excel for Investors, Contacts and Outreach, with a column-matching step and duplicate warnings.
- Export any list to Excel.

### Currency
- Multi-currency: each amount carries its own currency and unit (e.g. $ Mn for a US investor's ticket, ₹ Cr for a deal).
- No automatic conversion between currencies.

### Display formats
- Per-user setting for date format (DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD) and number grouping (Indian 12,34,56,789 or international 123,456,789).

### Backups
- Scheduled automatic daily backups (database plus Vault files) to a folder on the server, keeping the last N copies (N set in Settings).
- Manual "Download backup" and restore from a backup file in Settings.

### Devices and branding
- Desktop-first: optimised for laptop screens; works on phones but not polished.
- Dealflow branding only (no firm logo or themes).

### Modules
1. Investors
2. Contacts
3. Outreach
4. Active Deals
5. Tasks & Reminders
6. Vault

No separate Interactions module: interactions are captured as dated notes inside Outreach and Active Deals.

### 1. Investors
- Basics: firm name, type (PE, VC, family office, strategic, AIF, NBFC/debt fund, other), website, HQ city, country, short description.
- Investment preferences: sectors, stages, min/max ticket size, geography focus, equity/debt/both.
- Firm profile: fund size/AUM, current fund vintage, actively deploying (yes/no/unknown).
- Track record: notable portfolio companies and past deals (free text).
- Relationship: relationship owner (CRM user), strength (cold/warm/strong), last contacted date.
- **Linked Contacts: a dedicated section** listing the firm's individuals (the foundation of the Contacts module).
- Other: tags, notes.
- Deal history built automatically from the per-deal investor tracker.

### 2. Contacts
- One module for both investor-side and company-side people; each person linked to an investor firm or a deal company; filter to switch between the two.
- Basics: full name, designation, linked firm/company, department/team.
- Contact details: work email, personal email, mobile, office phone, LinkedIn URL, city.
- Role in decisions: decision-maker level (Partner/MD, Principal/VP, Associate/Analyst, Other); primary point of contact flag.
- Relationship: owner, strength, last contacted date, introduced by.
- Personal: birthday, assistant/EA name and contact, preferred channel (email/call/WhatsApp).
- Other: tags, notes.

### 3. Outreach (origination: approaching companies to win mandates)
- Company level (decided 29 Sep 2026): each company is recorded once, and every approach to it is an outreach attempt under that company. Pitching a company again after a lost mandate starts a new attempt; the company's details, contacts and earlier attempts stay together. Company basics and the financial snapshot are shared by all of a company's attempts (and later its deals).
- Stages: Identified → Contacted → Meeting held → Proposal sent → Negotiating → Mandate won.
- Exits from any stage: Lost (with reason) and On hold (with reason and revisit date). The exit stage is recorded. On hold resumes at the paused stage.
- Mandate won automatically moves the company into Active Deals (at Data collection), carrying all outreach data.
- Fields:
  - Company basics: name, sector, sub-sector, city, website, year founded, description.
  - Financial snapshot: latest revenue, EBITDA, PAT (with FY), current/last valuation.
  - Opportunity: transaction type (equity raise, debt raise, M&A sell-side, M&A buy-side, pre-IPO, other), expected size, expected timeline.
  - People: linked company contacts, lead banker, source (referral/inbound/cold/event/existing relationship).
  - Commercials: proposed retainer, proposed success fee %, final agreed fees.
  - Tracking: current stage, stage-entered date, next follow-up date, notes/interaction log.
  - Exit: lost reason, or on-hold reason and revisit date.
  - Tags.

### 4. Active Deals (execution: after mandate is won)
- No "Mandate signed" stage: a mandate won in Outreach is assumed signed and appears here automatically.
- Stages (fixed in V1):
  - Preparation: Data collection → First draft → Pre-finalisation → Finalisation → Investors identified
  - Execution: Investor outreach → Collaterals sent / data room access → Management meetings → Term sheet → Due diligence → Documentation → Closed
- Exits from any stage: Withdrawal by company, Dropped, On hold. The exit stage is recorded. On hold resumes at the paused stage.
- Per-deal investor tracker: investors from the Investors module mapped to the deal.
  - Statuses: Identified → Teaser sent → NDA signed → Collaterals / data room shared → Meeting held → Term sheet received → Selected
  - Exits: Declined (with reason) and No response.
  - Funnel counts shown per deal; feeds each investor's deal history.
- Investor matching (simple, rules-based):
  - Suggests a ranked list of investors for a deal, using filters plus a basic fit score.
  - Fit is based on fields already captured: sector, stage, ticket size vs deal size, geography, equity/debt, actively deploying.
  - One click adds a suggested investor to the deal's investor tracker.
- Fields (in addition to data carried from Outreach):
  - Mandate: mandate date, engagement letter reference, exclusive/non-exclusive, validity/expiry date.
  - Deal team: lead banker and team members with roles (MD, VP, Associate, Analyst).
  - Transaction terms: target raise, instrument (equity/CCPS/CCD/debt/mix), pre-money valuation expectation, primary vs secondary split, use of funds.
  - Fees: agreed retainer, retainer received (yes/no plus amounts), success fee %, minimum success fee.
  - Timeline: target close date, current stage-entered date.
  - Closure details: amount raised, final valuation, lead investor(s), closing date, fee earned.
  - Notes/interaction log; linked vault documents and tasks.
- Defaults chosen while building (29 Sep 2026, open to change):
  - Companies get a Geography field (shared with outreach and deals) so matching can compare it with investors' geography focus; the deal records its company stage (Seed, Growth, …) for the same reason.
  - CCPS and CCD count as equity for matching; Mix matches equity and debt investors.
  - Deal exits don't ask for a reason; an optional note goes into the deal's log.
  - Deals aren't imported from Excel: a pipeline sheet imported into Outreach at "Mandate won" creates the deals.

### 5. Tasks & Reminders
- Fields: title, description, due date and time, priority (low/medium/high), status (open/in progress/done), assigned to (one user), created by.
- Optionally linked to an outreach record, active deal, investor or contact (and shown on that record).
- Views: My tasks (today/overdue/upcoming); all tasks filterable by user, deal or status.
- In-app notifications for due and overdue tasks.

### 6. Vault
- File uploads only, stored on the firm's own server (Docker volume).
- Access: role-based permission plus a separate per-user Vault PIN.
- Document fields: title, category (NDA, Engagement letter, Teaser, IM, Financial model, Term sheet, DD report, Definitive agreement, Other), description, uploaded by, upload date, file type/size.
- Tagging: to a company (outreach/active deal) and/or an investor. One document can carry multiple tags.

---

## Not in V1 (to discuss after V1)
- Record-level access (restricting individual deals to specific users)
- Field-level permissions (hiding specific fields from specific roles)
- Custom fields created by admins
- Configurable pipeline stages (rename/add/reorder/remove)
- Separate Interactions module
- Email reminders and notifications (including emailed invites and password resets)
- Email and calendar sync (Gmail/Outlook)
- Vault links to external storage (Google Drive, SharePoint, Dropbox)
- Vault document version history
- Full dashboard: my tasks, outreach funnel, investor tracker totals, stale relationships, fees view
- Full audit log (history of every change, deletion and Vault download)
- Automatic currency conversion
- Advanced investor matching (learning from past deal outcomes)
- Mobile-optimised layout and installable phone app (PWA)
- Firm logo and light/dark themes
- Demo/sample data on first install
- VC-specific workflow and pipeline
- Desktop/offline app version
