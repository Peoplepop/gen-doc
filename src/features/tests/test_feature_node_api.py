"""HTTP-layer tests for FeatureNode CRUD, arbitrary-depth tree handling,
and hard-delete semantics (incl. the `parent` PROTECT safety net).

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: we call the endpoints through Django's test client and assert on
the HTTP response / DB final state, never on internal functions/classes
directly (same convention as `projects/tests/test_project_api.py`).
"""

import json

from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.test import Client, TestCase

from features.models import FeatureNode


class FeatureNodeAuthGateTests(TestCase):
    """未登入無法存取任何內容模組管理 API."""

    def test_list_requires_auth(self):
        response = self.client.get("/api/feature-nodes/")
        self.assertEqual(response.status_code, 401)

    def test_create_requires_auth(self):
        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "帳號管理"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_tree_requires_auth(self):
        response = self.client.get("/api/feature-nodes/tree/")
        self.assertEqual(response.status_code, 401)

    def test_detail_requires_auth(self):
        node = FeatureNode.objects.create(name="帳號管理")
        response = self.client.get(f"/api/feature-nodes/{node.id}/")
        self.assertEqual(response.status_code, 401)


class FeatureNodeCrudTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="admin1", password="pw12345")
        self.client.login(username="admin1", password="pw12345")

    def test_create_root_node(self):
        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "帳號管理", "description": "帳號相關功能"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "帳號管理")
        self.assertEqual(body["description"], "帳號相關功能")
        self.assertIsNone(body["parent"])

    def test_create_child_node_under_parent(self):
        parent = FeatureNode.objects.create(name="帳號管理")

        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "新增帳號", "parent": parent.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["parent"], parent.id)

    def test_create_missing_name_rejected(self):
        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"description": "沒有名字"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(FeatureNode.objects.count(), 0)

    def test_create_with_nonexistent_parent_rejected(self):
        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "新增帳號", "parent": 999999}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_update_node_fields(self):
        node = FeatureNode.objects.create(name="帳號管理", description="舊說明")

        response = self.client.patch(
            f"/api/feature-nodes/{node.id}/",
            data=json.dumps({"description": "新說明"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["description"], "新說明")
        node.refresh_from_db()
        self.assertEqual(node.description, "新說明")

    def test_update_parent_to_self_rejected(self):
        node = FeatureNode.objects.create(name="帳號管理")

        response = self.client.patch(
            f"/api/feature-nodes/{node.id}/",
            data=json.dumps({"parent": node.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_update_parent_to_own_descendant_rejected(self):
        root = FeatureNode.objects.create(name="帳號管理")
        child = FeatureNode.objects.create(name="新增帳號", parent=root)

        response = self.client.patch(
            f"/api/feature-nodes/{root.id}/",
            data=json.dumps({"parent": child.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)


class FeatureNodeArbitraryDepthTreeTests(TestCase):
    """證明「任意深度」不是只吃兩層——建一個 root -> child -> grandchild
    三層樹，驗證扁平列表與巢狀 tree 端點都能正確表達出這個深度。"""

    def setUp(self):
        self.user = User.objects.create_user(username="admin2", password="pw12345")
        self.client.login(username="admin2", password="pw12345")

        self.root = FeatureNode.objects.create(name="帳號管理")
        self.child = FeatureNode.objects.create(name="編輯帳號", parent=self.root)
        self.grandchild = FeatureNode.objects.create(
            name="編輯帳號權限", parent=self.child
        )

    def test_flat_list_exposes_full_parent_chain(self):
        response = self.client.get("/api/feature-nodes/")
        self.assertEqual(response.status_code, 200)

        by_id = {n["id"]: n for n in response.json()["results"]}
        self.assertEqual(by_id[self.root.id]["parent"], None)
        self.assertEqual(by_id[self.child.id]["parent"], self.root.id)
        self.assertEqual(by_id[self.grandchild.id]["parent"], self.child.id)

    def test_tree_endpoint_nests_three_levels_deep(self):
        response = self.client.get("/api/feature-nodes/tree/")
        self.assertEqual(response.status_code, 200)

        roots = response.json()["results"]
        self.assertEqual(len(roots), 1)
        root_node = roots[0]
        self.assertEqual(root_node["id"], self.root.id)
        self.assertEqual(len(root_node["children"]), 1)

        child_node = root_node["children"][0]
        self.assertEqual(child_node["id"], self.child.id)
        self.assertEqual(len(child_node["children"]), 1)

        grandchild_node = child_node["children"][0]
        self.assertEqual(grandchild_node["id"], self.grandchild.id)
        self.assertEqual(grandchild_node["children"], [])

    def test_tree_endpoint_handles_a_fourth_level(self):
        great_grandchild = FeatureNode.objects.create(
            name="編輯帳號權限-細項", parent=self.grandchild
        )

        response = self.client.get("/api/feature-nodes/tree/")
        roots = response.json()["results"]
        grandchild_node = roots[0]["children"][0]["children"][0]

        self.assertEqual(len(grandchild_node["children"]), 1)
        self.assertEqual(
            grandchild_node["children"][0]["id"], great_grandchild.id
        )


class FeatureNodeHardDeleteTests(TestCase):
    """節點刪除為真實刪除（無 soft-delete）：刪除後資料列本身不再存在。
    `parent` 使用 on_delete=PROTECT，仍有子孫的節點不可直接刪除。"""

    def setUp(self):
        self.user = User.objects.create_user(username="admin3", password="pw12345")
        self.client.login(username="admin3", password="pw12345")

    def test_delete_removes_row_permanently(self):
        node = FeatureNode.objects.create(name="帳號管理")
        node_id = node.id

        response = self.client.delete(f"/api/feature-nodes/{node_id}/")
        self.assertEqual(response.status_code, 200)

        self.assertFalse(FeatureNode.objects.filter(pk=node_id).exists())

    def test_deleted_node_hidden_from_list(self):
        node = FeatureNode.objects.create(name="帳號管理")
        self.client.delete(f"/api/feature-nodes/{node.id}/")

        response = self.client.get("/api/feature-nodes/")
        ids = [n["id"] for n in response.json()["results"]]
        self.assertNotIn(node.id, ids)

    def test_deleted_node_hidden_from_tree(self):
        node = FeatureNode.objects.create(name="帳號管理")
        self.client.delete(f"/api/feature-nodes/{node.id}/")

        response = self.client.get("/api/feature-nodes/tree/")
        ids = [n["id"] for n in response.json()["results"]]
        self.assertNotIn(node.id, ids)

    def test_deleted_node_detail_returns_404(self):
        node = FeatureNode.objects.create(name="帳號管理")
        self.client.delete(f"/api/feature-nodes/{node.id}/")

        response = self.client.get(f"/api/feature-nodes/{node.id}/")
        self.assertEqual(response.status_code, 404)

    def test_deleting_node_with_children_is_blocked_by_parent_protect(self):
        """`parent` 為 on_delete=PROTECT——仍有子孫的節點不可直接刪除，
        避免整棵子樹被連帶清除或憑空斷開。這是刻意保留的安全防線（見
        features/models.py 的 docstring），不是這次移除軟刪除要拿掉的
        東西：呼叫端必須先刪除或改接子孫節點才能刪掉父節點。"""
        parent = FeatureNode.objects.create(name="帳號管理")
        FeatureNode.objects.create(name="新增帳號", parent=parent)

        with self.assertRaises(ProtectedError):
            self.client.delete(f"/api/feature-nodes/{parent.id}/")

        # 刪除被擋下，父節點與子節點都必須還在。
        self.assertTrue(FeatureNode.objects.filter(pk=parent.id).exists())

    def test_deleting_leaf_child_then_parent_succeeds(self):
        parent = FeatureNode.objects.create(name="帳號管理")
        child = FeatureNode.objects.create(name="新增帳號", parent=parent)

        child_response = self.client.delete(f"/api/feature-nodes/{child.id}/")
        self.assertEqual(child_response.status_code, 200)

        parent_response = self.client.delete(f"/api/feature-nodes/{parent.id}/")
        self.assertEqual(parent_response.status_code, 200)

        self.assertFalse(FeatureNode.objects.filter(pk=parent.id).exists())
        self.assertFalse(FeatureNode.objects.filter(pk=child.id).exists())


class FeatureNodeCsrfProtectionTests(TestCase):
    """Create/update/delete 是會改變狀態的操作，必須受 CSRF 保護，不可用
    @csrf_exempt 抄捷徑（T2 review 時抓到過一次真的漏洞）。用
    enforce_csrf_checks=True 的 client——預設 test Client 會關掉 CSRF 檢查，
    抓不到這種回歸。"""

    def setUp(self):
        User.objects.create_user(username="admin4", password="pw12345")
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="admin4", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_create_without_csrf_token_is_rejected(self):
        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "帳號管理"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(FeatureNode.objects.count(), 0)

    def test_create_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()

        response = self.client.post(
            "/api/feature-nodes/",
            data=json.dumps({"name": "帳號管理"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 201)

    def test_delete_without_csrf_token_is_rejected(self):
        node = FeatureNode.objects.create(name="帳號管理")

        response = self.client.delete(f"/api/feature-nodes/{node.id}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(FeatureNode.objects.filter(pk=node.id).exists())
