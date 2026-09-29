"""The six V1 modules. Permissions are stored per role per module, including for
modules that are not built yet, so roles never need migrating when a module lands."""

MODULES = [
    ("investors", "Investors"),
    ("contacts", "Contacts"),
    ("outreach", "Outreach"),
    ("deals", "Active Deals"),
    ("tasks", "Tasks & Reminders"),
    ("vault", "Vault"),
]
MODULE_KEYS = [k for k, _ in MODULES]
MODULE_LABELS = dict(MODULES)

# Modules whose screens exist in this build. The frontend hides the rest.
# "companies" is part of Outreach: one record per company, with its outreach attempts under it.
BUILT_MODULES = ["investors", "contacts", "companies", "outreach"]

# Record types without their own permission row: allowed whatever any of these modules allow.
# Companies are shared by Outreach and (later) Active Deals.
DERIVED_PERMISSIONS = {"companies": ["outreach", "deals"]}

ACTIONS = ["view", "create", "edit", "delete"]
