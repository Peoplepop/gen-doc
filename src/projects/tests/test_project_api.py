"""HTTP-layer tests for Project CRUD, auth gating, soft delete, and the
optimistic-lock conflict scenario.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: we call the endpoints through Django's test client and assert on
the HTTP response / DB final state, never on internal functions/classes
directly.
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from projects.models import Project

SAMPLE_PROJECT_PAYLOAD = {
    "customer_name": "台積電",
    "project_name": "ITSM 導入專案",
    "system_name": "BMC Helix ITSM",
    "system_url": "https://itsm.example.com",
    "system_version": "22.1",
    "acceptance_date": "2026-09-01",
    "install_environment": "正式環境",
    "operating_system": "RHEL 9",
    "database": "PostgreSQL 15",
    "server_location": "內湖機房",
}


class ProjectAuthGateTests(TestCase):
    """未登入無法存取任何專案 API."""

    def test_list_requires_auth(self):
        response = self.client.get("/api/projects/")
        self.assertEqual(response.status_code, 401)

    def test_create_requires_auth(self):
        response = self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_detail_requires_auth(self):
        owner = User.objects.create_user(username="owner", password="pw12345")
        project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )

        response = self.client.get(f"/api/projects/{project.id}/")
        self.assertEqual(response.status_code, 401)

    def test_delete_requires_auth(self):
        owner = User.objects.create_user(username="owner2", password="pw12345")
        project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )

        response = self.client.delete(f"/api/projects/{project.id}/")
        self.assertEqual(response.status_code, 401)


class ProjectCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm", password="pw12345")
        self.client.login(username="pm", password="pw12345")

    def test_create_project_with_all_variable_fields(self):
        response = self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        for field, value in SAMPLE_PROJECT_PAYLOAD.items():
            self.assertEqual(body[field], value)

        project = Project.objects.get(pk=body["id"])
        self.assertEqual(project.owner, self.user)
        self.assertFalse(project.is_deleted)

    def test_create_project_missing_required_fields_rejected(self):
        response = self.client.post(
            "/api/projects/",
            data=json.dumps({"system_name": "Something"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Project.objects.count(), 0)

    def test_list_shows_created_project_with_key_fields(self):
        self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )

        response = self.client.get("/api/projects/")

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["customer_name"], "台積電")
        self.assertEqual(results[0]["project_name"], "ITSM 導入專案")
        self.assertIn("created_at", results[0])

    def test_soft_delete_hides_from_list_but_keeps_row_in_db(self):
        create_response = self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        project_id = create_response.json()["id"]

        delete_response = self.client.delete(f"/api/projects/{project_id}/")
        self.assertEqual(delete_response.status_code, 200)

        list_response = self.client.get("/api/projects/")
        self.assertEqual(list_response.json()["results"], [])

        # The row itself must still exist in the DB, only flagged deleted.
        project = Project.objects.get(pk=project_id)
        self.assertTrue(project.is_deleted)

    def test_update_project_succeeds_with_current_updated_at(self):
        create_response = self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        created = create_response.json()

        update_response = self.client.patch(
            f"/api/projects/{created['id']}/",
            data=json.dumps(
                {
                    "updated_at": created["updated_at"],
                    "system_version": "22.2",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["system_version"], "22.2")

    def test_update_without_updated_at_rejected(self):
        create_response = self.client.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        created = create_response.json()

        update_response = self.client.patch(
            f"/api/projects/{created['id']}/",
            data=json.dumps({"system_version": "22.2"}),
            content_type="application/json",
        )

        self.assertEqual(update_response.status_code, 400)


class OptimisticLockConflictTests(TestCase):
    """Given 使用者 A 與使用者 B 同時開啟同一個專案並各自修改，
    When A 先儲存成功、B 接著儲存，
    Then B 的儲存被系統擋下並提示「已被其他人更新，請重新整理」。
    """

    def setUp(self):
        User.objects.create_user(username="userA", password="pw-a-12345")
        User.objects.create_user(username="userB", password="pw-b-12345")

        self.client_a = Client()
        self.client_b = Client()
        self.client_a.login(username="userA", password="pw-a-12345")
        self.client_b.login(username="userB", password="pw-b-12345")

        create_response = self.client_a.post(
            "/api/projects/",
            data=json.dumps(SAMPLE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        self.project_id = create_response.json()["id"]

    def test_second_stale_save_is_rejected_with_conflict_message(self):
        # A and B both open (GET) the same project — each captures the
        # same updated_at "version" they loaded.
        view_a = self.client_a.get(f"/api/projects/{self.project_id}/").json()
        view_b = self.client_b.get(f"/api/projects/{self.project_id}/").json()
        self.assertEqual(view_a["updated_at"], view_b["updated_at"])

        # A saves first — succeeds, and updated_at advances.
        response_a = self.client_a.patch(
            f"/api/projects/{self.project_id}/",
            data=json.dumps(
                {
                    "updated_at": view_a["updated_at"],
                    "system_version": "A 的版本",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response_a.status_code, 200)
        new_updated_at = response_a.json()["updated_at"]
        self.assertNotEqual(new_updated_at, view_a["updated_at"])

        # B then saves using their now-stale updated_at — must be rejected.
        response_b = self.client_b.patch(
            f"/api/projects/{self.project_id}/",
            data=json.dumps(
                {
                    "updated_at": view_b["updated_at"],
                    "system_version": "B 的版本",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response_b.status_code, 409)
        self.assertIn("已被其他人更新，請重新整理", response_b.json()["detail"])

        # A's write must not have been silently clobbered.
        project = Project.objects.get(pk=self.project_id)
        self.assertEqual(project.system_version, "A 的版本")

    def test_b_can_save_after_refreshing_updated_at(self):
        view_a = self.client_a.get(f"/api/projects/{self.project_id}/").json()
        self.client_a.patch(
            f"/api/projects/{self.project_id}/",
            data=json.dumps(
                {"updated_at": view_a["updated_at"], "system_version": "A 的版本"}
            ),
            content_type="application/json",
        )

        # B refreshes (re-fetches) before retrying their save.
        refreshed = self.client_b.get(f"/api/projects/{self.project_id}/").json()
        response_b = self.client_b.patch(
            f"/api/projects/{self.project_id}/",
            data=json.dumps(
                {
                    "updated_at": refreshed["updated_at"],
                    "system_version": "B 的版本（重新整理後）",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(
            response_b.json()["system_version"], "B 的版本（重新整理後）"
        )
