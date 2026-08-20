"""HTTP-layer tests for T4: 功能選擇（樹狀勾選＋子孫自動包含＋排除清單）.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: we call the endpoint through Django's test client and assert on the
HTTP response / DB final state (same convention as
`projects/tests/test_project_api.py` and
`features/tests/test_feature_node_api.py`).
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from features.models import FeatureNode
from projects.models import Project
from selections.models import ProjectFeatureSelection


def _selection_url(project_id):
    return f"/api/projects/{project_id}/feature-selection/"


class FeatureSelectionAuthGateTests(TestCase):
    """未登入無法存取功能選擇 API."""

    def setUp(self):
        owner = User.objects.create_user(username="owner", password="pw12345")
        self.project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )

    def test_get_requires_auth(self):
        response = self.client.get(_selection_url(self.project.id))
        self.assertEqual(response.status_code, 401)

    def test_put_requires_auth(self):
        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [], "excluded": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class FeatureSelectionCascadeIncludeTests(TestCase):
    """勾選「登入」節點時，其所有子孫節點自動被包含在該專案的功能選擇中
    （Issue #5 acceptance criterion 1）——含多層子孫（root -> child ->
    grandchild），不是只吃兩層。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm", password="pw12345")
        self.client.login(username="pm", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

        self.login_root = FeatureNode.objects.create(name="登入")
        self.login_child = FeatureNode.objects.create(
            name="單一登入", parent=self.login_root
        )
        self.login_grandchild = FeatureNode.objects.create(
            name="單一登入設定細項", parent=self.login_child
        )
        # 不相關的節點，勾選「登入」後不該出現在有效清單。
        self.unrelated = FeatureNode.objects.create(name="報表")

    def test_checking_root_includes_full_multi_level_subtree(self):
        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.login_root.id], "excluded": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["checked"], [self.login_root.id])
        self.assertEqual(
            sorted(body["effective"]),
            sorted(
                [
                    self.login_root.id,
                    self.login_child.id,
                    self.login_grandchild.id,
                ]
            ),
        )
        self.assertNotIn(self.unrelated.id, body["effective"])

    def test_get_reflects_previously_saved_cascade(self):
        self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.login_root.id], "excluded": []}),
            content_type="application/json",
        )

        response = self.client.get(_selection_url(self.project.id))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            sorted(body["effective"]),
            sorted(
                [
                    self.login_root.id,
                    self.login_child.id,
                    self.login_grandchild.id,
                ]
            ),
        )


class FeatureSelectionExcludeListTests(TestCase):
    """已勾選「帳號管理」後，將其子孫「刪除帳號確認」加入排除清單，該子孫
    不再算作本專案已選功能，但「帳號管理」其餘子孫仍算已選（Issue #5
    acceptance criterion 2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm2", password="pw12345")
        self.client.login(username="pm2", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

        self.account_mgmt = FeatureNode.objects.create(name="帳號管理")
        self.create_account = FeatureNode.objects.create(
            name="新增帳號", parent=self.account_mgmt
        )
        self.edit_account = FeatureNode.objects.create(
            name="編輯帳號", parent=self.account_mgmt
        )
        self.delete_confirm = FeatureNode.objects.create(
            name="刪除帳號確認", parent=self.account_mgmt
        )

    def test_excluding_one_descendant_keeps_siblings_selected(self):
        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps(
                {
                    "checked": [self.account_mgmt.id],
                    "excluded": [self.delete_confirm.id],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        effective = set(body["effective"])

        self.assertNotIn(self.delete_confirm.id, effective)
        self.assertIn(self.account_mgmt.id, effective)
        self.assertIn(self.create_account.id, effective)
        self.assertIn(self.edit_account.id, effective)

    def test_excluded_list_persists_and_is_queryable(self):
        self.client.put(
            _selection_url(self.project.id),
            data=json.dumps(
                {
                    "checked": [self.account_mgmt.id],
                    "excluded": [self.delete_confirm.id],
                }
            ),
            content_type="application/json",
        )

        response = self.client.get(_selection_url(self.project.id))
        body = response.json()
        self.assertEqual(body["excluded"], [self.delete_confirm.id])

    def test_excluding_a_node_does_not_remove_its_own_children(self):
        """排除只移除該節點本身，不連帶砍掉它自己的子孫。"""
        grandchild = FeatureNode.objects.create(
            name="刪除帳號確認-次要提示", parent=self.delete_confirm
        )

        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps(
                {
                    "checked": [self.account_mgmt.id],
                    "excluded": [self.delete_confirm.id],
                }
            ),
            content_type="application/json",
        )

        effective = set(response.json()["effective"])
        self.assertNotIn(self.delete_confirm.id, effective)
        self.assertIn(grandchild.id, effective)


class FeatureSelectionTwoProjectIsolationTests(TestCase):
    """建立客戶 A 專案並勾選登入/帳號管理/報表，查詢僅包含這三項（及其未
    被排除的子孫）；建立客戶 B 專案並勾選登入/權限管理/查詢，查詢結果與
    客戶 A 互不影響、各自獨立（Issue #5 acceptance criteria 3、4）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm3", password="pw12345")
        self.client.login(username="pm3", password="pw12345")

        self.login = FeatureNode.objects.create(name="登入")
        self.account_mgmt = FeatureNode.objects.create(name="帳號管理")
        self.reports = FeatureNode.objects.create(name="報表")
        self.permission_mgmt = FeatureNode.objects.create(name="權限管理")
        self.query = FeatureNode.objects.create(name="查詢")

        self.project_a = Project.objects.create(
            owner=self.user, customer_name="客戶 A", project_name="A 專案"
        )
        self.project_b = Project.objects.create(
            owner=self.user, customer_name="客戶 B", project_name="B 專案"
        )

    def test_customer_a_selection_contains_exactly_the_three_checked_roots(self):
        response = self.client.put(
            _selection_url(self.project_a.id),
            data=json.dumps(
                {
                    "checked": [
                        self.login.id,
                        self.account_mgmt.id,
                        self.reports.id,
                    ],
                    "excluded": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        effective = set(response.json()["effective"])
        self.assertEqual(
            effective, {self.login.id, self.account_mgmt.id, self.reports.id}
        )
        self.assertNotIn(self.permission_mgmt.id, effective)
        self.assertNotIn(self.query.id, effective)

    def test_customer_b_selection_is_independent_of_customer_a(self):
        self.client.put(
            _selection_url(self.project_a.id),
            data=json.dumps(
                {
                    "checked": [
                        self.login.id,
                        self.account_mgmt.id,
                        self.reports.id,
                    ],
                    "excluded": [],
                }
            ),
            content_type="application/json",
        )

        response_b = self.client.put(
            _selection_url(self.project_b.id),
            data=json.dumps(
                {
                    "checked": [
                        self.login.id,
                        self.permission_mgmt.id,
                        self.query.id,
                    ],
                    "excluded": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response_b.status_code, 200)
        effective_b = set(response_b.json()["effective"])
        self.assertEqual(
            effective_b, {self.login.id, self.permission_mgmt.id, self.query.id}
        )

        # 客戶 A 的選擇沒有被客戶 B 的寫入影響到。
        response_a = self.client.get(_selection_url(self.project_a.id))
        effective_a = set(response_a.json()["effective"])
        self.assertEqual(
            effective_a, {self.login.id, self.account_mgmt.id, self.reports.id}
        )
        self.assertNotIn(self.permission_mgmt.id, effective_a)
        self.assertNotIn(self.query.id, effective_a)


class FeatureSelectionDisabledNodeTests(TestCase):
    """已停用（is_enabled=False）的功能節點不可被加入功能選擇——對應 T3
    acceptance criteria「新專案的功能選擇頁看不到、無法新勾選已停用節點」。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="pm4", password="pw12345")
        self.client.login(username="pm4", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

    def test_checking_a_disabled_node_is_rejected(self):
        node = FeatureNode.objects.create(name="舊功能", is_enabled=False)

        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [node.id], "excluded": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            ProjectFeatureSelection.objects.filter(
                project=self.project, node=node
            ).exists()
        )

    def test_disabling_a_previously_checked_node_removes_it_from_effective(self):
        node = FeatureNode.objects.create(name="即將停用")
        self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [node.id], "excluded": []}),
            content_type="application/json",
        )

        node.is_enabled = False
        node.save(update_fields=["is_enabled"])

        response = self.client.get(_selection_url(self.project.id))
        self.assertNotIn(node.id, response.json()["effective"])

    def test_checking_nonexistent_node_is_rejected(self):
        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [999999], "excluded": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class FeatureSelectionReplaceSemanticsTests(TestCase):
    """PUT 是整組覆寫，不是逐一累加——第二次 PUT 未包含的舊勾選會被移除。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm5", password="pw12345")
        self.client.login(username="pm5", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node_a = FeatureNode.objects.create(name="功能 A")
        self.node_b = FeatureNode.objects.create(name="功能 B")

    def test_second_put_replaces_first(self):
        self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.node_a.id], "excluded": []}),
            content_type="application/json",
        )

        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.node_b.id], "excluded": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["checked"], [self.node_b.id])
        self.assertNotIn(self.node_a.id, body["effective"])


class FeatureSelectionCsrfProtectionTests(TestCase):
    """寫入端點必須受 CSRF 保護，不可用 @csrf_exempt 抄捷徑（T2 review 時
    抓到過一次真的漏洞）。用 enforce_csrf_checks=True 的 client——預設
    test Client 會關掉 CSRF 檢查，抓不到這種回歸。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm6", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="功能 A")

        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm6", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_put_without_csrf_token_is_rejected(self):
        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.node.id], "excluded": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectFeatureSelection.objects.filter(project=self.project).exists()
        )

    def test_put_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()

        response = self.client.put(
            _selection_url(self.project.id),
            data=json.dumps({"checked": [self.node.id], "excluded": []}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
