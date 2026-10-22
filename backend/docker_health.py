"""Container health check (Docker HEALTHCHECK / compose). `web`: the API answers /api/health/.
`scheduler`: the scheduler wrote its heartbeat in the last five minutes."""

import os
import sys
import time
import urllib.request

what = sys.argv[1] if len(sys.argv) > 1 else "web"
if what == "scheduler":
    path = os.path.join(os.environ.get("DEALFLOW_DATA_DIR", "/data"), "scheduler.alive")
    try:
        sys.exit(0 if time.time() - os.path.getmtime(path) < 300 else 1)
    except OSError:
        sys.exit(1)
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health/", timeout=4) as r:  # nosec B310
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
