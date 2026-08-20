"""HTTP-layer tests for T6: 覆寫可視化清單——管理者在後台編輯某節點的共用
內容時，可以查看目前有哪些專案對這個節點/這個文件類型設有覆寫（唯讀，不
做任何自動比對或升級，Issue #7 acceptance criterion 4）。
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase

from features.models import FeatureNode
from projects.models import Project


def _node_overrides_url(node_id):
    return f"/api/feature-nodes/{node_id}/overrides/"


def _node_overrides_by_type_url(node_id, document_type):
    return f"/api/feature-nodes/{node_id}/overrides/{document_type}/"


def _override_detail_url(project_id, node_id, document_type):
    return f"/api/projects/{project_id}/content-overrides/{node_id}/{document_type}/"


class OverrideVisibilityAuthGateTests(TestCase):
    def test_list_requires_auth(self):
        node = FeatureNode.objects.create(name="帳號管理")
        response = self.client.get(_node_overrides_url(node.id))
        self.assertEqual(response.status_code, 401)


class OverrideVisibilityListTests(TestCase):
    """核心驗收條件：能查詢「哪些專案對此節點/此文件類型設有覆寫」；沒有
    任何覆寫的節點/文件類型，清單為空（Issue #7 acceptance criterion 4）。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="admin1", password="pw12345")
        self.client.login(username="admin1", password="pw12345")

        self.node = FeatureNode.objects.create(name="帳號管理")
        self.other_node = FeatureNode.objects.create(name="報表")

        self.project_a = Project.objects.create(
            owner=self.user, customer_name="客戶 A", project_name="A 專案"
        )
        self.project_b = Project.objects.create(
            owner=self.user, customer_name="客戶 B", project_name="B 專案"
        )

    def test_empty_visibility_list_when_nobody_has_overridden(self):
        response = self.client.get(_node_overrides_url(self.node.id))
        self.assertEqual(response.status_code, 200)
        body = response.json()

        by_type = {r["document_type"]: r for r in body["results"]}
        self.assertEqual(len(by_type), 4)
        for entry in by_type.values():
            self.assertEqual(entry["projects"], [])

    def test_visibility_list_shows_projects_that_override_this_node_and_type(self):
        self.client.put(
            _override_detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "A 的覆寫"}),
            content_type="application/json",
        )
        self.client.put(
            _override_detail_url(self.project_b.id, self.node.id, "system_test"),
            data=json.dumps({"body": "B 的覆寫"}),
            content_type="application/json",
        )

        response = self.client.get(
            _node_overrides_by_type_url(self.node.id, "system_test")
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        project_ids = {p["id"] for p in body["projects"]}
        self.assertEqual(project_ids, {self.project_a.id, self.project_b.id})

    def test_visibility_list_is_scoped_to_the_specific_document_type(self):
        """對 system_test 設定覆寫，不會讓 training_deck 的可視化清單也
        顯示這個專案——覆寫可視化清單跟覆寫本身一樣，是 per document_type
        的（Issue #7 acceptance criterion 1、2 的可視化對應版本）。"""
        self.client.put(
            _override_detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "A 的覆寫"}),
            content_type="application/json",
        )

        response = self.client.get(_node_overrides_url(self.node.id))
        by_type = {r["document_type"]: r for r in response.json()["results"]}

        self.assertEqual(len(by_type["system_test"]["projects"]), 1)
        self.assertEqual(by_type["training_deck"]["projects"], [])
        self.assertEqual(by_type["system_design"]["projects"], [])
        self.assertEqual(by_type["system_install"]["projects"], [])

    def test_visibility_list_is_scoped_to_the_specific_node(self):
        """對某個節點設定覆寫，不會出現在另一個節點的可視化清單裡。"""
        self.client.put(
            _override_detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "A 的覆寫"}),
            content_type="application/json",
        )

        response = self.client.get(
            _node_overrides_by_type_url(self.other_node.id, "system_test")
        )
        self.assertEqual(response.json()["projects"], [])

    def test_deleting_override_removes_project_from_visibility_list(self):
        self.client.put(
            _override_detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "A 的覆寫"}),
            content_type="application/json",
        )
        self.client.delete(
            _override_detail_url(self.project_a.id, self.node.id, "system_test")
        )

        response = self.client.get(
            _node_overrides_by_type_url(self.node.id, "system_test")
        )
        self.assertEqual(response.json()["projects"], [])

    def test_unknown_document_type_rejected(self):
        response = self.client.get(
            _node_overrides_by_type_url(self.node.id, "not_a_real_type")
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_node_rejected(self):
        response = self.client.get(_node_overrides_url(999999))
        self.assertEqual(response.status_code, 404)
