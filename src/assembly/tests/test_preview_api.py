"""HTTP-layer tests for T7: 文件組裝引擎＋文件預覽頁 (Issue #8).

Per the MVP spec's Testing Decisions, the HTTP API layer is the only test
seam — every acceptance criterion below is verified by calling the endpoint
through Django's test client and asserting on the HTTP response / DB final
state (same convention as the other five apps' tests).
"""

import json
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from features.models import FeatureNode, FeatureNodeContent
from overrides.models import ProjectCustomSection, ProjectFeatureContentOverride
from projects.models import Project
from screenshots.models import ProjectScreenshot, ScreenshotRequirement
from selections.models import ProjectFeatureExclusion, ProjectFeatureSelection

from ..models import custom_section_key, node_section_key

_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="gen_doc_assembly_test_")
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _valid_png(name="shot.png"):
    return SimpleUploadedFile(
        name, _PNG_HEADER + b"\x00" * 32, content_type="image/png"
    )


def tearDownModule():
    shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)


def _preview_url(project_id, document_type):
    return f"/api/projects/{project_id}/preview/{document_type}/"


def _adjustments_url(project_id, document_type):
    return f"/api/projects/{project_id}/preview/{document_type}/adjustments/"


def _validate_url(project_id, document_type):
    return f"/api/projects/{project_id}/preview/{document_type}/validate/"


def _sections_by_key(response_json):
    return {s["key"]: s for s in response_json["sections"]}


class PreviewAuthGateTests(TestCase):
    """未登入無法存取任何文件預覽／組裝 API（同其他 app 的既有慣例）。"""

    def setUp(self):
        owner = User.objects.create_user(username="owner1", password="pw12345")
        self.project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )

    def test_get_preview_requires_auth(self):
        response = self.client.get(_preview_url(self.project.id, "system_test"))
        self.assertEqual(response.status_code, 401)

    def test_validate_requires_auth(self):
        response = self.client.get(_validate_url(self.project.id, "system_test"))
        self.assertEqual(response.status_code, 401)

    def test_adjustments_requires_auth(self):
        response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps({"section_order": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class SelectionFilteringTests(TestCase):
    """Issue #8 acceptance criteria 2、3：未勾選的功能節點（含子孫）完全不
    出現在任一文件類型的預覽組裝結果中；被排除的子孫節點即使父節點被勾
    選，也不出現，但其餘子孫仍出現。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm1", password="pw12345")
        self.client.login(username="pm1", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

        self.root = FeatureNode.objects.create(name="帳號管理")
        self.child_edit = FeatureNode.objects.create(name="編輯帳號", parent=self.root)
        self.child_delete = FeatureNode.objects.create(
            name="刪除帳號確認", parent=self.root
        )
        self.unchecked_root = FeatureNode.objects.create(name="報表")

        for node in (self.root, self.child_edit, self.child_delete, self.unchecked_root):
            FeatureNodeContent.objects.create(
                node=node, document_type="system_test", body=f"{node.name} 測試內容"
            )

        ProjectFeatureSelection.objects.create(project=self.project, node=self.root)
        ProjectFeatureExclusion.objects.create(
            project=self.project, node=self.child_delete
        )

    def test_unchecked_node_never_appears(self):
        response = self.client.get(_preview_url(self.project.id, "system_test"))
        self.assertEqual(response.status_code, 200)
        keys = _sections_by_key(response.json())
        self.assertNotIn(node_section_key(self.unchecked_root.id), keys)

    def test_excluded_descendant_absent_but_sibling_present(self):
        response = self.client.get(_preview_url(self.project.id, "system_test"))
        keys = _sections_by_key(response.json())
        self.assertNotIn(node_section_key(self.child_delete.id), keys)
        self.assertIn(node_section_key(self.child_edit.id), keys)
        self.assertIn(node_section_key(self.root.id), keys)


class VariableSubstitutionTests(TestCase):
    """Issue #8 acceptance criterion 1：節點內容含
    `{{project_variables.系統網址}}` 佔位符，專案已填寫系統網址時，預覽結
    果中佔位符被替換為實際值。同時驗證英文欄位代碼 key
    （`{{project_variables.system_url}}`）也能正確解析到同一個值——這是本
    票對 Issue #8 範例（中文標籤）與 Issue #1 一般敘述（xxx 代碼）之間落差
    的設計判斷。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm2", password="pw12345")
        self.client.login(username="pm2", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user,
            customer_name="客戶",
            project_name="專案",
            system_url="https://portal.example.com",
        )
        self.node = FeatureNode.objects.create(name="登入")
        FeatureNodeContent.objects.create(
            node=self.node,
            document_type="system_test",
            body=(
                "系統網址(中文key)：{{project_variables.系統網址}}；"
                "系統網址(英文key)：{{project_variables.system_url}}"
            ),
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def test_placeholder_substituted_with_actual_value(self):
        response = self.client.get(_preview_url(self.project.id, "system_test"))
        body = json.loads(response.content)
        section = _sections_by_key(body)[node_section_key(self.node.id)]
        self.assertIn("https://portal.example.com", section["body"])
        self.assertNotIn("{{project_variables", section["body"])
        self.assertEqual(section["missing_variables"], [])
        self.assertEqual(body["missing_variables"], [])


class MissingVariableTests(TestCase):
    """缺少對應變數值時，佔位符維持原始字面文字（不是被替換成空字串），
    並在組裝結果與驗證端點中被明確列為缺漏（Issue #1：比照缺少截圖的警告
    機制處理）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm3", password="pw12345")
        self.client.login(username="pm3", password="pw12345")
        # system_version 刻意留空。
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="安裝")
        FeatureNodeContent.objects.create(
            node=self.node,
            document_type="system_test",
            body="系統版本：{{project_variables.system_version}}",
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def test_missing_variable_left_literal_and_reported(self):
        response = self.client.get(_preview_url(self.project.id, "system_test"))
        body = json.loads(response.content)
        section = _sections_by_key(body)[node_section_key(self.node.id)]
        self.assertIn("{{project_variables.system_version}}", section["body"])
        self.assertIn("system_version", section["missing_variables"])
        self.assertIn("system_version", body["missing_variables"])

    def test_validate_endpoint_blocks_on_missing_variable(self):
        response = self.client.get(_validate_url(self.project.id, "system_test"))
        result = response.json()
        self.assertFalse(result["ready"])
        self.assertIn("system_version", result["missing_variables"])


class PreviewPersistenceTests(TestCase):
    """Issue #8 acceptance criterion 4：使用者在文件預覽頁調整章節順序後
    離開頁面，重新進入該專案的文件預覽頁時，顯示上次調整過的順序——同時
    覆蓋「是否包含某章節」、「換一張截圖」、「改圖說」三種調整也都持久化。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="pm4", password="pw12345")
        self.client.login(username="pm4", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node_a = FeatureNode.objects.create(name="功能 A")
        self.node_b = FeatureNode.objects.create(name="功能 B")
        for node in (self.node_a, self.node_b):
            FeatureNodeContent.objects.create(
                node=node, document_type="system_test", body=f"{node.name} 內容"
            )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node_a)
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node_b)
        self.custom_section = ProjectCustomSection.objects.create(
            project=self.project, title="自訂章節", content="自訂內容"
        )

        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node_a, name="帳號列表"
        )

    def test_reorder_persists_across_requests(self):
        new_order = [
            custom_section_key(self.custom_section.id),
            node_section_key(self.node_b.id),
            node_section_key(self.node_a.id),
        ]
        put_response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps({"section_order": new_order}),
            content_type="application/json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(
            [s["key"] for s in put_response.json()["sections"]], new_order
        )

        # 模擬「離開頁面、重新進入」：另一次獨立的 GET 請求。
        get_response = self.client.get(_preview_url(self.project.id, "system_test"))
        self.assertEqual([s["key"] for s in get_response.json()["sections"]], new_order)

    def test_toggle_inclusion_persists(self):
        put_response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps(
                {"excluded_sections": [node_section_key(self.node_a.id)]}
            ),
            content_type="application/json",
        )
        self.assertEqual(put_response.status_code, 200)
        sections = _sections_by_key(put_response.json())
        self.assertFalse(sections[node_section_key(self.node_a.id)]["included"])
        self.assertTrue(sections[node_section_key(self.node_b.id)]["included"])

        get_response = self.client.get(_preview_url(self.project.id, "system_test"))
        sections = _sections_by_key(get_response.json())
        self.assertFalse(sections[node_section_key(self.node_a.id)]["included"])

    @override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
    def test_replace_shown_screenshot_and_edit_caption_persist(self):
        # 預設版本刻意只套用 training_deck，讓 system_test 底下「自動判斷」
        # 結果原本會是 applicable=False——這樣才能明確驗證「手動指定」真的
        # 覆蓋了自動判斷，而不是巧合命中同一張圖。
        default_shot = ProjectScreenshot.objects.create(
            project=self.project,
            requirement=self.requirement,
            image=_valid_png("default.png"),
            original_filename="default.png",
            document_types=["training_deck"],
            is_custom=False,
            caption="預設圖說",
        )

        before = self.client.get(_preview_url(self.project.id, "system_test")).json()
        before_section = _sections_by_key(before)[node_section_key(self.node_a.id)]
        self.assertFalse(before_section["screenshots"][0]["applicable"])

        put_response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps(
                {
                    "screenshot_selections": {str(self.requirement.id): default_shot.id},
                    "caption_overrides": {str(self.requirement.id): "手動指定的圖說"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(put_response.status_code, 200)
        section = _sections_by_key(put_response.json())[node_section_key(self.node_a.id)]
        shot_entry = section["screenshots"][0]
        self.assertTrue(shot_entry["applicable"])
        self.assertEqual(shot_entry["screenshot"]["id"], default_shot.id)
        self.assertEqual(shot_entry["caption"], "手動指定的圖說")

        # 重新進入預覽頁：調整仍然生效。
        get_response = self.client.get(_preview_url(self.project.id, "system_test"))
        section = _sections_by_key(get_response.json())[node_section_key(self.node_a.id)]
        shot_entry = section["screenshots"][0]
        self.assertTrue(shot_entry["applicable"])
        self.assertEqual(shot_entry["caption"], "手動指定的圖說")

    def test_partial_adjustment_does_not_clobber_other_settings(self):
        self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps(
                {"excluded_sections": [node_section_key(self.node_a.id)]}
            ),
            content_type="application/json",
        )
        # 第二次 PUT 只調整排序，沒有帶 excluded_sections —— 應維持第一次
        # 設定的排除狀態，不會被清空。
        new_order = [
            node_section_key(self.node_b.id),
            node_section_key(self.node_a.id),
            custom_section_key(self.custom_section.id),
        ]
        response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps({"section_order": new_order}),
            content_type="application/json",
        )
        sections = _sections_by_key(response.json())
        self.assertFalse(sections[node_section_key(self.node_a.id)]["included"])


class MissingScreenshotValidationTests(TestCase):
    """Issue #8 acceptance criterion 5：某截圖需求尚未上傳時，產生文件前系
    統明確列出缺少哪些截圖，並擋下產生動作。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm5", password="pw12345")
        self.client.login(username="pm5", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")
        FeatureNodeContent.objects.create(
            node=self.node, document_type="system_test", body="內容"
        )
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表"
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def test_missing_screenshot_reported_with_detail(self):
        response = self.client.get(_validate_url(self.project.id, "system_test"))
        result = response.json()
        self.assertFalse(result["ready"])
        self.assertEqual(len(result["missing_screenshots"]), 1)
        missing = result["missing_screenshots"][0]
        self.assertEqual(missing["requirement_id"], self.requirement.id)
        self.assertEqual(missing["requirement_name"], "帳號列表")
        self.assertEqual(missing["node"], self.node.id)

    def test_excluding_section_removes_it_from_missing_screenshot_check(self):
        self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps(
                {"excluded_sections": [node_section_key(self.node.id)]}
            ),
            content_type="application/json",
        )
        response = self.client.get(_validate_url(self.project.id, "system_test"))
        result = response.json()
        self.assertTrue(result["ready"])
        self.assertEqual(result["missing_screenshots"], [])


class DocumentTypeIndependenceTests(TestCase):
    """Issue #8 acceptance criterion 6：各文件類型的組裝各自獨立，使用該文
    件類型專屬的節點內容，不同文件類型互不影響彼此的組裝結果。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm6", password="pw12345")
        self.client.login(username="pm6", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="登入")
        FeatureNodeContent.objects.create(
            node=self.node, document_type="system_test", body="系統測試共用預設內容"
        )
        FeatureNodeContent.objects.create(
            node=self.node,
            document_type="training_deck",
            bullets=["教育訓練條列 1", "教育訓練條列 2"],
        )
        ProjectFeatureContentOverride.objects.create(
            project=self.project,
            node=self.node,
            document_type="system_test",
            body="系統測試專案專屬覆寫內容",
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def test_override_in_one_document_type_does_not_leak_to_another(self):
        system_test_response = self.client.get(
            _preview_url(self.project.id, "system_test")
        )
        system_test_section = _sections_by_key(system_test_response.json())[
            node_section_key(self.node.id)
        ]
        self.assertEqual(system_test_section["content_source"], "override")
        self.assertEqual(system_test_section["body"], "系統測試專案專屬覆寫內容")

        training_deck_response = self.client.get(
            _preview_url(self.project.id, "training_deck")
        )
        training_deck_section = _sections_by_key(training_deck_response.json())[
            node_section_key(self.node.id)
        ]
        self.assertEqual(training_deck_section["content_source"], "default")
        self.assertEqual(
            training_deck_section["bullets"], ["教育訓練條列 1", "教育訓練條列 2"]
        )
        self.assertNotIn("系統測試專案專屬覆寫內容", json.dumps(training_deck_section))

    def test_preview_adjustments_are_independent_per_document_type(self):
        self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps(
                {"excluded_sections": [node_section_key(self.node.id)]}
            ),
            content_type="application/json",
        )

        system_test_response = self.client.get(
            _preview_url(self.project.id, "system_test")
        )
        training_deck_response = self.client.get(
            _preview_url(self.project.id, "training_deck")
        )

        system_test_section = _sections_by_key(system_test_response.json())[
            node_section_key(self.node.id)
        ]
        training_deck_section = _sections_by_key(training_deck_response.json())[
            node_section_key(self.node.id)
        ]
        self.assertFalse(system_test_section["included"])
        self.assertTrue(training_deck_section["included"])


class AdjustmentsCsrfProtectionTests(TestCase):
    """調整端點是一個會改變狀態的寫入端點，必須受 CSRF 保護（絕不可
    `@csrf_exempt`——T2 已因此在 review 被抓出漏洞）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm7", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm7", password="pw12345")

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_adjustments_without_csrf_token_is_rejected(self):
        response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps({"section_order": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_adjustments_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()
        response = self.client.put(
            _adjustments_url(self.project.id, "system_test"),
            data=json.dumps({"section_order": []}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)


class InvalidDocumentTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm8", password="pw12345")
        self.client.login(username="pm8", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

    def test_unknown_document_type_is_404(self):
        response = self.client.get(_preview_url(self.project.id, "not_a_real_type"))
        self.assertEqual(response.status_code, 404)
