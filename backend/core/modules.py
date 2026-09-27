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
BUILT_MODULES = ["investors"]

ACTIONS = ["view", "create", "edit", "delete"]
