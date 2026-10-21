"""The notification bell's "Clear all": kept per person, replaced on each clear, validated."""

from .base import APITestBase


class ClearedAlertsTests(APITestBase):
    def test_round_trip_and_per_person(self):
        self.assertEqual(self.client.get("/api/notifications/cleared/").json(), {"keys": []})
        keys = ["task:3:overdue", "stuck:deals:5:term_sheet", "task:3:overdue"]
        r = self.client.put("/api/notifications/cleared/", {"keys": keys}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["keys"], ["task:3:overdue", "stuck:deals:5:term_sheet"])
        other = self.client_for(self.make_user("o@firm.com", self.make_role("Viewer")))
        self.assertEqual(other.get("/api/notifications/cleared/").json(), {"keys": []})
        self.client.put("/api/notifications/cleared/", {"keys": []}, format="json")
        self.assertEqual(self.client.get("/api/notifications/cleared/").json(), {"keys": []})

    def test_bad_input(self):
        for body in ({"keys": "x"}, {"keys": [1]}, {"keys": [""]}, {"keys": ["x" * 121]}, {}, {"keys": None}):
            self.assertEqual(self.client.put("/api/notifications/cleared/", body, format="json").status_code, 400, body)
        r = self.client.put("/api/notifications/cleared/", {"keys": [f"k{i}" for i in range(1500)]}, format="json")
        self.assertEqual(len(r.json()["keys"]), 1000)

    def test_needs_sign_in(self):
        from rest_framework.test import APIClient

        self.assertIn(APIClient().get("/api/notifications/cleared/").status_code, (401, 403))
