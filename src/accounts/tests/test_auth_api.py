"""HTTP-layer tests for the login/logout endpoints.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: we call the endpoints through Django's test client and assert on
the HTTP response, never on internal functions/classes directly.
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase


class LoginApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="s3cret-pw")

    def test_login_with_valid_credentials_succeeds(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "alice", "password": "s3cret-pw"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")
        # Session cookie should now be authenticated for subsequent requests.
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_with_invalid_password_rejected(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "alice", "password": "wrong-pw"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_with_unknown_username_rejected(self):
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "nobody", "password": "whatever"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)

    def test_logout_clears_session(self):
        self.client.login(username="alice", password="s3cret-pw")
        self.assertIn("_auth_user_id", self.client.session)

        response = self.client.post("/api/auth/logout/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
