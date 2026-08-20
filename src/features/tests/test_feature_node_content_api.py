"""HTTP-layer tests for per-document-type FeatureNodeContent: the three
DOCX-family document types (系統設計/系統測試/系統安裝) use prose text,
教育訓練簡報 (PPTX) uses a structured bullet-item array — genuinely
different shapes, no auto-conversion between them.
"""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from features.models import FeatureNode, FeatureNodeContent


class FeatureNodeContentAuthGateTests(TestCase):
    def test_content_list_requires_auth(self):
        node = FeatureNode.objects.create(name="帳號管理")
        response = self.client.get(f"/api/feature-nodes/{node.id}/contents/")
        self.assertEqual(response.status_code, 401)

    def test_content_detail_requires_auth(self):
        node = FeatureNode.objects.create(name="帳號管理")
        response = self.client.put(
            f"/api/feature-nodes/{node.id}/contents/system_test/",
            data=json.dumps({"body": "測試內容"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class FeatureNodeProseContentTests(TestCase):
    """三類 DOCX 文件（系統設計/系統測試/系統安裝）使用一般文字內容。"""

    def setUp(self):
        self.user = User.objects.create_user(username="admin5", password="pw12345")
        self.client.login(username="admin5", password="pw12345")
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_content_list_returns_all_four_document_types_with_defaults(self):
        response = self.client.get(f"/api/feature-nodes/{self.node.id}/contents/")
        self.assertEqual(response.status_code, 200)

        results = response.json()["results"]
        doc_types = [r["document_type"] for r in results]
        self.assertEqual(
            doc_types,
            ["system_design", "system_test", "system_install", "training_deck"],
        )
        for entry in results:
            self.assertEqual(entry["body"], "")
            self.assertEqual(entry["bullets"], [])

    def test_set_prose_content_for_system_design(self):
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_design/",
            data=json.dumps({"body": "本模組提供帳號的新增、編輯、刪除功能。"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["body"], "本模組提供帳號的新增、編輯、刪除功能。"
        )
        self.assertEqual(body["bullets"], [])

        content = FeatureNodeContent.objects.get(
            node=self.node, document_type="system_design"
        )
        self.assertEqual(
            content.body, "本模組提供帳號的新增、編輯、刪除功能。"
        )
        self.assertEqual(content.bullets, [])

    def test_each_document_type_content_is_independent(self):
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_design/",
            data=json.dumps({"body": "設計文件內容"}),
            content_type="application/json",
        )
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_test/",
            data=json.dumps({"body": "測試文件內容"}),
            content_type="application/json",
        )
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_install/",
            data=json.dumps({"body": "安裝文件內容"}),
            content_type="application/json",
        )

        response = self.client.get(f"/api/feature-nodes/{self.node.id}/contents/")
        by_type = {r["document_type"]: r["body"] for r in response.json()["results"]}

        self.assertEqual(by_type["system_design"], "設計文件內容")
        self.assertEqual(by_type["system_test"], "測試文件內容")
        self.assertEqual(by_type["system_install"], "安裝文件內容")

    def test_updating_prose_content_overwrites_previous_value(self):
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_test/",
            data=json.dumps({"body": "第一版"}),
            content_type="application/json",
        )
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_test/",
            data=json.dumps({"body": "第二版"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["body"], "第二版")
        self.assertEqual(
            FeatureNodeContent.objects.filter(
                node=self.node, document_type="system_test"
            ).count(),
            1,
        )

    def test_unknown_document_type_rejected(self):
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/not_a_real_type/",
            data=json.dumps({"body": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)


class FeatureNodeTrainingBulletContentTests(TestCase):
    """教育訓練簡報使用條列式內容（一組標題＋條列項目，而非整段文字）。
    這組測試證明它的資料形狀是「結構化陣列」，跟其他三類的散文欄位截然
    不同，且系統不會把貼上的一整段文字自動轉成條列。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="admin6", password="pw12345")
        self.client.login(username="admin6", password="pw12345")
        self.node = FeatureNode.objects.create(name="帳號管理")

    def test_set_bullet_content_for_training_deck(self):
        bullets = ["帳號管理總覽", "如何新增帳號", "如何編輯帳號權限"]

        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/training_deck/",
            data=json.dumps({"bullets": bullets}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["bullets"], bullets)
        self.assertEqual(body["body"], "")
        self.assertIsInstance(body["bullets"], list)

        content = FeatureNodeContent.objects.get(
            node=self.node, document_type="training_deck"
        )
        self.assertEqual(content.bullets, bullets)
        self.assertEqual(content.body, "")

    def test_bullet_content_shape_differs_from_prose_content(self):
        """教育訓練 (bullets: list) 與其他三類 (body: str) 是不同的資料
        形狀，不是同一個欄位重複使用。"""
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_design/",
            data=json.dumps({"body": "一整段散文說明帳號管理的設計。"}),
            content_type="application/json",
        )
        self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/training_deck/",
            data=json.dumps({"bullets": ["重點一", "重點二"]}),
            content_type="application/json",
        )

        response = self.client.get(f"/api/feature-nodes/{self.node.id}/contents/")
        by_type = {r["document_type"]: r for r in response.json()["results"]}

        design_entry = by_type["system_design"]
        training_entry = by_type["training_deck"]

        self.assertIsInstance(design_entry["body"], str)
        self.assertNotIsInstance(design_entry["body"], list)
        self.assertEqual(design_entry["bullets"], [])

        self.assertIsInstance(training_entry["bullets"], list)
        self.assertEqual(training_entry["bullets"], ["重點一", "重點二"])
        self.assertEqual(training_entry["body"], "")

    def test_pasting_a_prose_paragraph_as_bullets_string_is_rejected(self):
        """核心驗收條件：教育訓練內容以條列項目輸入，不接受直接貼一整段
        文字後自動轉條列。送一個字串（不是陣列）到 bullets 欄位必須被拒絕，
        系統不可以自動幫忙用換行或句號切成條列陣列。"""
        prose_paragraph = (
            "本模組提供帳號管理總覽。介紹如何新增帳號。說明如何編輯帳號權限。"
        )

        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/training_deck/",
            data=json.dumps({"bullets": prose_paragraph}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            FeatureNodeContent.objects.filter(
                node=self.node, document_type="training_deck"
            ).exists()
        )

    def test_missing_bullets_field_rejected_not_defaulted_from_body(self):
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/training_deck/",
            data=json.dumps({"body": "有人誤填了 body 而不是 bullets"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_blank_bullet_items_rejected(self):
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/training_deck/",
            data=json.dumps({"bullets": ["正常項目", "   "]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class FeatureNodeContentCsrfProtectionTests(TestCase):
    def setUp(self):
        User.objects.create_user(username="admin7", password="pw12345")
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="admin7", password="pw12345")
        self.node = FeatureNode.objects.create(name="帳號管理")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_set_content_without_csrf_token_is_rejected(self):
        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_test/",
            data=json.dumps({"body": "測試內容"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            FeatureNodeContent.objects.filter(
                node=self.node, document_type="system_test"
            ).exists()
        )

    def test_set_content_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()

        response = self.client.put(
            f"/api/feature-nodes/{self.node.id}/contents/system_test/",
            data=json.dumps({"body": "測試內容"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
