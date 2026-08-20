"""HTTP-layer tests for the health-check endpoint.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: we call the endpoint through Django's test client and assert on the
HTTP response, never on internal functions/classes directly.
"""

from django.test import TestCase


class HealthCheckApiTests(TestCase):
    def test_health_check_returns_ok_status(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
