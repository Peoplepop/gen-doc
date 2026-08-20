"""HTTP-layer tests for T6: 專案自訂章節（ProjectCustomSection）——不對應
任何 FeatureNode，只屬於單一專案（Issue #7 acceptance criterion 3）。
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from overrides.models import ProjectCustomSection
from projects.models import Project


def _list_url(project_id):
    return f"/api/projects/{project_id}/custom-sections/"


def _detail_url(project_id, section_id):
    return f"/api/projects/{project_id}/custom-sections/{section_id}/"


class CustomSectionAuthGateTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="owner2", password="pw12345")
        self.project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )

    def test_list_requires_auth(self):
        response = self.client.get(_list_url(self.project.id))
        self.assertEqual(response.status_code, 401)

    def test_create_requires_auth(self):
        response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"title": "自訂章節"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class CustomSectionCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm5", password="pw12345")
        self.client.login(username="pm5", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

    def test_create_and_list_custom_section(self):
        response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps(
                {"title": "客戶專屬合規聲明", "content": "本系統符合...", "order": 1}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["title"], "客戶專屬合規聲明")
        self.assertEqual(created["project"], self.project.id)

        list_response = self.client.get(_list_url(self.project.id))
        results = list_response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "客戶專屬合規聲明")

    def test_create_requires_title(self):
        response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"content": "沒有標題"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_update_section_via_patch(self):
        create_response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"title": "初版標題", "content": "初版內容"}),
            content_type="application/json",
        )
        section_id = create_response.json()["id"]

        response = self.client.patch(
            _detail_url(self.project.id, section_id),
            data=json.dumps({"content": "修改後內容", "order": 5}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "初版標題")
        self.assertEqual(body["content"], "修改後內容")
        self.assertEqual(body["order"], 5)

    def test_delete_section(self):
        create_response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"title": "待刪除章節"}),
            content_type="application/json",
        )
        section_id = create_response.json()["id"]

        response = self.client.delete(_detail_url(self.project.id, section_id))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ProjectCustomSection.objects.filter(pk=section_id).exists()
        )


class CustomSectionProjectScopingTests(TestCase):
    """自訂章節只屬於建立它的專案——另一個專案的清單看不到它（Issue #7
    acceptance criterion 3：只屬於本專案）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm6", password="pw12345")
        self.client.login(username="pm6", password="pw12345")
        self.project_a = Project.objects.create(
            owner=self.user, customer_name="客戶 A", project_name="A 專案"
        )
        self.project_b = Project.objects.create(
            owner=self.user, customer_name="客戶 B", project_name="B 專案"
        )

    def test_second_project_custom_section_list_excludes_first_projects_section(self):
        self.client.post(
            _list_url(self.project_a.id),
            data=json.dumps({"title": "只屬於 A 的章節"}),
            content_type="application/json",
        )

        response_b = self.client.get(_list_url(self.project_b.id))
        self.assertEqual(response_b.json()["results"], [])

        response_a = self.client.get(_list_url(self.project_a.id))
        self.assertEqual(len(response_a.json()["results"]), 1)

    def test_cannot_access_another_projects_section_by_id(self):
        create_response = self.client.post(
            _list_url(self.project_a.id),
            data=json.dumps({"title": "只屬於 A 的章節"}),
            content_type="application/json",
        )
        section_id = create_response.json()["id"]

        response = self.client.get(_detail_url(self.project_b.id, section_id))
        self.assertEqual(response.status_code, 404)


class CustomSectionCsrfProtectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm7", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm7", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_create_without_csrf_token_is_rejected(self):
        response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"title": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectCustomSection.objects.filter(project=self.project).exists())

    def test_create_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()

        response = self.client.post(
            _list_url(self.project.id),
            data=json.dumps({"title": "x"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
