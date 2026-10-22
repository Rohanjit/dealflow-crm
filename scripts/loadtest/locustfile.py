"""Many bankers at once (load testing; see backend/core/management/commands/seed_load.py).

    locust -f scripts/loadtest/locustfile.py --host http://127.0.0.1:8000 --headless \
        --csv out/run --csv-full-history

Each simulated banker signs in for real (password + authenticator code), then browses lists, opens
records, searches, edits now and then, and polls the notification bell like the app does (every
minute, marked as background). The load rises in steps (StepShape) until it breaks.
"""

import base64
import hashlib
import hmac
import itertools
import os
import random
import struct
import time

from locust import FastHttpUser, LoadTestShape, between, events, task

SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
PASSWORD = "Load-test-pass-1"
PEOPLE = int(os.environ.get("LT_PEOPLE", "200"))
STEPS = [int(x) for x in os.environ.get("LT_STEPS", "10,25,50,100,200").split(",")]
STEP_SECONDS = int(os.environ.get("LT_STEP_SECONDS", "120"))
WRITES = os.environ.get("LT_WRITES", "1") == "1"
_next = itertools.count()
MAX_ID = {"investors": 100000, "contacts": 400000, "deals": 8000, "outreach": 80000, "companies": 50000}
SCALE = float(os.environ.get("LT_SCALE", "20")) / 20


def totp(step_offset=0):
    key = base64.b32decode(SECRET)
    counter = int(time.time() // 30) + step_offset
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def rid(module):
    return random.randint(1, max(1, int(MAX_ID[module] * SCALE)))


class Banker(FastHttpUser):
    wait_time = between(3, 10)  # a person reading a screen between clicks

    def on_start(self):
        n = next(_next) % PEOPLE
        self.email = f"banker{n}@loadtest.example"
        self.client.get("/", name="app page")
        self.csrf = self.client.cookiejar._cookies.get("127.0.0.1", {}).get("/", {}).get("csrftoken")
        self.csrf = self.csrf.value if self.csrf else ""
        r = self.client.post("/api/auth/login/", json={"email": self.email, "password": PASSWORD},
                             headers=self.h(), name="sign in: password")
        self.csrf = self._csrf() or self.csrf
        for attempt in range(3):
            r = self.client.post("/api/auth/mfa/verify/", json={"method": "app", "code": totp(attempt)}, headers=self.h(),
                                 name="sign in: code")
            if r.status_code == 200:
                break
        self.csrf = self._csrf() or self.csrf
        self.last_bell = 0

    def _csrf(self):
        for c in self.client.cookiejar:
            if c.name.endswith("csrftoken"):
                return c.value
        return ""

    def h(self, background=False):
        h = {"X-CSRFToken": self.csrf, "Referer": self.host + "/"}
        if background:
            h["X-Dealflow-Background"] = "1"
        return h

    def get(self, url, name, background=False):
        # A restricted deal (403) or a deleted record (404) is a correct answer, not a failure.
        with self.client.get(url, headers=self.h(background), name=name, catch_response=True) as r:
            if r.status_code in (403, 404):
                r.success()
            return r

    @task(1)
    def bell(self):
        if time.time() - self.last_bell < 60:
            return
        self.last_bell = time.time()
        for url, name in (("/api/tasks/alerts/", "bell: tasks"), ("/api/insights/alerts/", "bell: insights"),
                          ("/api/stale/", "bell: going cold"), ("/api/access-requests/", "bell: access"),
                          ("/api/sync/agenda/", "bell: meetings"), ("/api/rooms/alerts/", "bell: rooms"),
                          ("/api/auth/security-alerts/", "bell: sign-ins"), ("/api/notifications/cleared/", "bell: cleared")):
            self.get(url, name, background=True)

    @task(6)
    def browse_list(self):
        mod = random.choice(["investors", "contacts", "companies", "outreach", "deals", "tasks"])
        q = random.choice(["?page=1", f"?page={random.randint(1, 50)}", "?ordering=-updated_at",
                           "?q=" + random.choice(["apex", "meera", "cedar", "pharma"])])
        if mod == "tasks":
            q = "?view=mine"
        self.get(f"/api/m/{mod}/{q}", f"list {mod}")

    @task(6)
    def open_record(self):
        mod = random.choice(["investors", "contacts", "deals", "outreach"])
        pk = rid(mod)
        self.get(f"/api/m/{mod}/{pk}/", f"open {mod}")
        if mod in ("deals", "outreach", "investors"):
            self.get(f"/api/m/{mod}/{pk}/notes/", f"log {mod}")
        if mod == "deals":
            self.get(f"/api/m/deal_investors/?deal=deals:{pk}&page_size=500", "deal tracker")
            self.get(f"/api/deals/{pk}/room/", "deal room")

    @task(2)
    def home(self):
        self.get("/api/dashboard/", "dashboard")

    @task(2)
    def search(self):
        self.get("/api/search/?q=" + random.choice(["apex", "blue", "meera rao", "pharma", "cedar ventures"]), "search")

    @task(1)
    def insights(self):
        self.get("/api/insights/analytics/?period=12", "insights")

    @task(2)
    def write(self):
        if not WRITES:
            return
        pk = rid("investors")
        self.client.patch(f"/api/m/investors/{pk}/", json={"hq_city": random.choice(["Pune", "Mumbai", "Delhi"])},
                          headers=self.h(), name="edit investor")
        d = rid("deals")
        self.client.post(f"/api/m/deals/{d}/notes/", json={"kind": "note", "text": "Spoke to the founder.",
                                                          "date": "2026-10-22"}, headers=self.h(), name="add note")


class StepShape(LoadTestShape):
    """10, 25, 50, 100, 200 bankers (LT_STEPS), each step held for LT_STEP_SECONDS."""

    def tick(self):
        t = self.get_run_time()
        i = int(t // STEP_SECONDS)
        if i >= len(STEPS):
            return None
        return STEPS[i], max(2, STEPS[i] // 10)


@events.quitting.add_listener
def _(environment, **kw):
    environment.process_exit_code = 0
