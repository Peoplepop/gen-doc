"""HTTP-layer tests for T5's admin-side CRUD:
`ScreenshotRequirement`（掛在 FeatureNode 上的截圖需求定義）.

Per the MVP spec's Testing Decisions, the HTTP API layer is the only test
seam (same convention as `features/tests/test_feature_node_api.py` and
`selections/tests/test_feature_selection_api.py`).
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from features.models import FeatureNode

from ..models import ScreenshotRequirement


def _list_url(node_id):
    return f"/api/feature-nodes/{node_id}/screenshot-requirements/"


def _detail_url(requirement_id):
    return f"/api/screenshot-requirements/{requirement_id}/"


class ScreenshotRequirementAuthGateTests(TestCase):
    def setUp(self):
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_list_requires_auth(self):
        response = self.client.get(_list_url(self.node.id))
        self.assertEqual(response.status_code, 401)

    def test_create_requires_auth(self):
        response = self.client.post(
            _list_url(self.node.id),
            data=json.dumps({"name": "帳號列表"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class ScreenshotRequirementCrudTests(TestCase):
    """Issue #6 acceptance criterion 1 的前提：「帳號管理」節點需要能定義
    「帳號列表／新增帳號／編輯帳號／刪除帳號確認」四個截圖需求項目。"""

    def setUp(self):
        self.user = User.objects.create_user(username="admin1", password="pw12345")
        self.client.login(username="admin1", password="pw12345")
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_create_and_list_requirements_in_order(self):
        names = ["帳號列表", "新增帳號", "編輯帳號", "刪除帳號確認"]
        for order, name in enumerate(names):
            response = self.client.post(
                _list_url(self.node.id),
                data=json.dumps({"name": name, "order": order}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 201, response.content)

        response = self.client.get(_list_url(self.node.id))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([r["name"] for r in body["results"]], names)

    def test_create_missing_name_is_rejected(self):
        response = self.client.post(
            _list_url(self.node.id),
            data=json.dumps({"order": 0}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_update_requirement_name_and_order(self):
        requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表", order=0
        )

        response = self.client.patch(
            _detail_url(requirement.id),
            data=json.dumps({"name": "帳號清單", "order": 5}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "帳號清單")
        self.assertEqual(body["order"], 5)

    def test_delete_soft_disables_and_hides_from_default_listing(self):
        requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="刪除帳號確認", order=3
        )

        response = self.client.delete(_detail_url(requirement.id))
        self.assertEqual(response.status_code, 200)

        requirement.refresh_from_db()
        self.assertFalse(requirement.is_enabled)

        listed = self.client.get(_list_url(self.node.id)).json()["results"]
        self.assertNotIn(requirement.id, [r["id"] for r in listed])

        listed_all = self.client.get(
            _list_url(self.node.id) + "?include_disabled=true"
        ).json()["results"]
        self.assertIn(requirement.id, [r["id"] for r in listed_all])

    def test_get_nonexistent_requirement_404s(self):
        response = self.client.get(_detail_url(999999))
        self.assertEqual(response.status_code, 404)


class ScreenshotRequirementCsrfProtectionTests(TestCase):
    """寫入端點必須受 CSRF 保護（同 selections app 的既有慣例，不可
    @csrf_exempt 抄捷徑）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="admin2", password="pw12345")
        self.node = FeatureNode.objects.create(name="帳號管理")
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="admin2", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_create_without_csrf_token_is_rejected(self):
        response = self.client.post(
            _list_url(self.node.id),
            data=json.dumps({"name": "帳號列表"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ScreenshotRequirement.objects.filter(node=self.node).exists()
        )

    def test_create_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()
        response = self.client.post(
            _list_url(self.node.id),
            data=json.dumps({"name": "帳號列表"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
