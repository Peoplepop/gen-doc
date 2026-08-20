"""HTTP-layer tests for T6: 專案內容覆寫（ProjectFeatureContentOverride）.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: call the endpoint through Django's test client, assert on the HTTP
response / DB final state (same convention as
`features/tests/test_feature_node_content_api.py` and
`selections/tests/test_feature_selection_api.py`).
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from features.models import FeatureNode, FeatureNodeContent
from overrides.models import ProjectFeatureContentOverride
from projects.models import Project


def _detail_url(project_id, node_id, document_type):
    return f"/api/projects/{project_id}/content-overrides/{node_id}/{document_type}/"


def _list_url(project_id):
    return f"/api/projects/{project_id}/content-overrides/"


class ContentOverrideAuthGateTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="owner1", password="pw12345")
        self.project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_list_requires_auth(self):
        response = self.client.get(_list_url(self.project.id))
        self.assertEqual(response.status_code, 401)

    def test_detail_get_requires_auth(self):
        response = self.client.get(_detail_url(self.project.id, self.node.id, "system_test"))
        self.assertEqual(response.status_code, 401)

    def test_detail_put_requires_auth(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": "覆寫內容"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class ContentOverrideScopingTests(TestCase):
    """核心驗收條件：設定 (project, node, "system_test") 的覆寫只影響這個
    組合本身——同一個節點的其他文件類型（例如 training_deck）仍使用共用
    預設；不影響其他專案（Issue #7 acceptance criteria 1、2）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm1", password="pw12345")
        self.client.login(username="pm1", password="pw12345")

        self.project_a = Project.objects.create(
            owner=self.user, customer_name="客戶 A", project_name="A 專案"
        )
        self.project_b = Project.objects.create(
            owner=self.user, customer_name="客戶 B", project_name="B 專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")

        FeatureNodeContent.objects.create(
            node=self.node, document_type="system_test", body="共用測試內容"
        )
        FeatureNodeContent.objects.create(
            node=self.node,
            document_type="training_deck",
            bullets=["共用重點一", "共用重點二"],
        )

    def test_setting_override_for_system_test_only_affects_that_document_type(self):
        response = self.client.put(
            _detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "專案 A 覆寫的測試內容"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["has_override"])
        self.assertEqual(body["effective"]["source"], "override")
        self.assertEqual(body["effective"]["body"], "專案 A 覆寫的測試內容")

        # 產生測試文件時使用專案覆寫的內容（透過讀取端點驗證，而非只斷言
        # 內部 DB 狀態）。
        test_response = self.client.get(
            _detail_url(self.project_a.id, self.node.id, "system_test")
        )
        self.assertEqual(
            test_response.json()["effective"]["body"], "專案 A 覆寫的測試內容"
        )

        # 產生教育訓練簡報（未被覆寫）時仍使用共用預設內容。
        training_response = self.client.get(
            _detail_url(self.project_a.id, self.node.id, "training_deck")
        )
        training_body = training_response.json()
        self.assertFalse(training_body["has_override"])
        self.assertEqual(training_body["effective"]["source"], "default")
        self.assertEqual(
            training_body["effective"]["bullets"], ["共用重點一", "共用重點二"]
        )

        # 同節點的 system_design / system_install 也完全不受影響——不存在
        # 覆寫，仍回退到（空的）共用預設。
        for other_type in ("system_design", "system_install"):
            other_response = self.client.get(
                _detail_url(self.project_a.id, self.node.id, other_type)
            )
            other_body = other_response.json()
            self.assertFalse(other_body["has_override"])
            self.assertEqual(other_body["effective"]["source"], "none")

    def test_override_does_not_affect_other_projects(self):
        self.client.put(
            _detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "只屬於 A 的覆寫"}),
            content_type="application/json",
        )

        response_b = self.client.get(
            _detail_url(self.project_b.id, self.node.id, "system_test")
        )
        body_b = response_b.json()
        self.assertFalse(body_b["has_override"])
        self.assertEqual(body_b["effective"]["body"], "共用測試內容")

    def test_list_endpoint_only_returns_existing_overrides(self):
        response = self.client.get(_list_url(self.project_a.id))
        self.assertEqual(response.json()["results"], [])

        self.client.put(
            _detail_url(self.project_a.id, self.node.id, "system_test"),
            data=json.dumps({"body": "覆寫"}),
            content_type="application/json",
        )

        response = self.client.get(_list_url(self.project_a.id))
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["node"], self.node.id)
        self.assertEqual(results[0]["document_type"], "system_test")


class ContentOverrideBulletShapeValidationTests(TestCase):
    """覆寫的 bullets-vs-prose 形狀驗證必須跟 features 的規則一致——送一個
    字串進教育訓練簡報覆寫的 bullets 欄位必須被拒絕，理由相同：不接受把
    一整段文字自動轉條列（Issue #1 DOCX/PPTX Output Spec）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm2", password="pw12345")
        self.client.login(username="pm2", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_bullets_override_accepts_list(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "training_deck"),
            data=json.dumps({"bullets": ["重點一", "重點二"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["override"]["bullets"], ["重點一", "重點二"])
        self.assertEqual(body["override"]["body"], "")

    def test_pasting_a_prose_string_into_bullets_override_is_rejected(self):
        prose_paragraph = "本模組提供帳號管理總覽。介紹如何新增帳號。"

        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "training_deck"),
            data=json.dumps({"bullets": prose_paragraph}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            ProjectFeatureContentOverride.objects.filter(
                project=self.project, node=self.node, document_type="training_deck"
            ).exists()
        )

    def test_blank_bullet_items_in_override_rejected(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "training_deck"),
            data=json.dumps({"bullets": ["正常項目", "   "]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_prose_override_rejects_non_string_body(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": ["不該是陣列"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_document_type_rejected(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "not_a_real_type"),
            data=json.dumps({"body": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


class ContentOverrideDeleteAndDecoupleTests(TestCase):
    """刪除覆寫後改回共用預設；修改共用內容不會自動同步既有覆寫（Issue #7
    acceptance criterion 5：覆寫與共用預設彼此獨立）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm3", password="pw12345")
        self.client.login(username="pm3", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="登入")
        self.default_content = FeatureNodeContent.objects.create(
            node=self.node, document_type="system_test", body="共用測試內容 v1"
        )

    def test_editing_shared_default_does_not_change_existing_override(self):
        self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": "專案覆寫內容"}),
            content_type="application/json",
        )

        # 修改共用內容一次（模擬管理者在後台編輯共用測試內容）。
        self.default_content.body = "共用測試內容 v2（已修改）"
        self.default_content.save()

        response = self.client.get(
            _detail_url(self.project.id, self.node.id, "system_test")
        )
        body = response.json()

        # 覆寫本身沒有被自動同步或改寫。
        self.assertEqual(body["override"]["body"], "專案覆寫內容")
        # 有效內容仍然是覆寫（覆寫優先），但 default 這欄反映出共用內容
        # 確實已經是新版——用來證明兩者是分開讀取、不是覆寫時複製了一份
        # 共用內容進去。
        self.assertEqual(body["default"]["body"], "共用測試內容 v2（已修改）")
        self.assertEqual(body["effective"]["body"], "專案覆寫內容")

    def test_deleting_override_falls_back_to_shared_default(self):
        self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": "專案覆寫內容"}),
            content_type="application/json",
        )

        response = self.client.delete(
            _detail_url(self.project.id, self.node.id, "system_test")
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["has_override"])
        self.assertEqual(body["effective"]["source"], "default")
        self.assertEqual(body["effective"]["body"], "共用測試內容 v1")

        self.assertFalse(
            ProjectFeatureContentOverride.objects.filter(
                project=self.project, node=self.node, document_type="system_test"
            ).exists()
        )


class ContentOverrideCsrfProtectionTests(TestCase):
    """寫入端點必須受 CSRF 保護（同 T2 review 抓到的既有規則，不可用
    @csrf_exempt 抄捷徑）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm4", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="功能 A")

        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm4", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_put_without_csrf_token_is_rejected(self):
        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ProjectFeatureContentOverride.objects.filter(project=self.project).exists()
        )

    def test_put_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()

        response = self.client.put(
            _detail_url(self.project.id, self.node.id, "system_test"),
            data=json.dumps({"body": "x"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
