"""`manage.py check --deploy` warnings for Dealflow's own security settings (decision 63)."""

from django.conf import settings
from django.core.checks import Warning, register


@register("security", deploy=True)
def antivirus_check(app_configs, **kwargs):
    out = []
    if getattr(settings, "ANTIVIRUS", "required") == "off":
        out.append(Warning("Virus scanning is switched off (DEALFLOW_ANTIVIRUS=off on a development server).",
                           hint="Remove DEALFLOW_ANTIVIRUS and set DEALFLOW_CLAMD.", id="dealflow.W001"))
    elif not getattr(settings, "CLAMD_ADDRESS", ""):
        out.append(Warning("No virus scanner is set up, so every upload will be refused.",
                           hint="Set DEALFLOW_CLAMD to clamd's host:port or socket (Docker Compose does this).",
                           id="dealflow.W002"))
    return out
