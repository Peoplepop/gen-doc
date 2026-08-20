"""HTTP-layer tests for T10 (Issue #11): 專案複製.

Per the MVP spec's Testing Decisions, the only test seam is the HTTP API
layer: exercise `POST /api/projects/<id>/duplicate/` and assert on the
response plus the *read endpoints* of the apps whose rows get copied
(selections/overrides/screenshots/assembly), not on internal DB state
alone — mirrors the existing T2/T6/T7 test style in this repo.
"""

import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from features.models import FeatureNode
from overrides.models import ProjectCustomSection, ProjectFeatureContentOverride
from projects.models import Project
from screenshots.models import ProjectScreenshot, ScreenshotRequirement

SOURCE_PROJECT_PAYLOAD = {
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

# Minimal-but-valid PNG signature so `_validate_image_upload` accepts it.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 20


class ProjectDuplicateFullyConfiguredScenarioTests(TestCase):
    """Given 一個已設定完整的專案（功能勾選/排除、內容覆寫、自訂章節、
    預覽設定、截圖都齊全），When 複製它，Then 新專案繼承前四項、但截圖、
    專案變數、預覽設定裡指向截圖的部分都不繼承，且原專案完全不受影響。
    """

    def setUp(self):
        self.user = User.objects.create_user(username="pm", password="pw12345")
        self.client.login(username="pm", password="pw12345")

        self.root = FeatureNode.objects.create(name="登入")
        self.excluded_child = FeatureNode.objects.create(
            name="登入子節點（將被排除）", parent=self.root
        )

        create_resp = self.client.post(
            "/api/projects/",
            data=json.dumps(SOURCE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 201)
        self.source_id = create_resp.json()["id"]

        # 功能勾選 + 排除清單 (T4)
        selection_resp = self.client.put(
            f"/api/projects/{self.source_id}/feature-selection/",
            data=json.dumps(
                {"checked": [self.root.id], "excluded": [self.excluded_child.id]}
            ),
            content_type="application/json",
        )
        self.assertEqual(selection_resp.status_code, 200)

        # 內容覆寫 (T6)
        override_resp = self.client.put(
            f"/api/projects/{self.source_id}/content-overrides/{self.root.id}/system_test/",
            data=json.dumps({"body": "覆寫內容：這是專案專屬的測試說明"}),
            content_type="application/json",
        )
        self.assertEqual(override_resp.status_code, 200)

        # 自訂章節 (T6)
        section_resp = self.client.post(
            f"/api/projects/{self.source_id}/custom-sections/",
            data=json.dumps(
                {"title": "客戶專屬合規聲明", "content": "自訂章節內容", "order": 5}
            ),
            content_type="application/json",
        )
        self.assertEqual(section_resp.status_code, 201)
        self.source_section_id = section_resp.json()["id"]

        # 截圖需求 + 上傳一張預設截圖 (T5) —— 複製時刻意不複製這個。
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.root, name="登入畫面截圖"
        )
        upload_resp = self.client.post(
            f"/api/projects/{self.source_id}/screenshots/{self.requirement.id}/default/",
            data={
                "image": SimpleUploadedFile("login.png", PNG_BYTES, content_type="image/png"),
                "caption": "原圖說",
            },
        )
        self.assertEqual(upload_resp.status_code, 201)
        self.source_shot_id = upload_resp.json()["id"]

        # 預覽設定 (T7)：排序含節點章節與自訂章節、排除自訂章節、手動指定
        # 截圖與圖說覆蓋（這兩個刻意不該被複製，見 views.py 的判斷說明）。
        node_key = f"node:{self.root.id}"
        custom_key = f"custom:{self.source_section_id}"
        preview_resp = self.client.put(
            f"/api/projects/{self.source_id}/preview/system_test/adjustments/",
            data=json.dumps(
                {
                    "section_order": [custom_key, node_key],
                    "excluded_sections": [custom_key],
                    "screenshot_selections": {
                        str(self.requirement.id): self.source_shot_id
                    },
                    "caption_overrides": {str(self.requirement.id): "覆蓋圖說"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(preview_resp.status_code, 200)

        # 複製動作本身。
        self.duplicate_resp = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新客戶專案"}),
            content_type="application/json",
        )
        self.new_project = self.duplicate_resp.json()
        self.new_id = self.new_project.get("id")

    def test_duplicate_returns_201_with_new_project_id(self):
        self.assertEqual(self.duplicate_resp.status_code, 201)
        self.assertIsNotNone(self.new_id)
        self.assertNotEqual(self.new_id, self.source_id)

    def test_new_project_variables_blank_except_supplied_required_fields(self):
        self.assertEqual(self.new_project["customer_name"], "新客戶")
        self.assertEqual(self.new_project["project_name"], "新客戶專案")
        # 除了呼叫端提供的兩個必填欄位，其餘專案變數一律留空——不繼承原
        # 專案的客戶名稱等資料（Issue #11 acceptance criterion 3）。
        self.assertEqual(self.new_project["system_name"], "")
        self.assertEqual(self.new_project["system_url"], "")
        self.assertEqual(self.new_project["system_version"], "")
        self.assertIsNone(self.new_project["acceptance_date"])
        self.assertEqual(self.new_project["install_environment"], "")
        self.assertEqual(self.new_project["operating_system"], "")
        self.assertEqual(self.new_project["database"], "")
        self.assertEqual(self.new_project["server_location"], "")

    def test_new_project_inherits_feature_selection_and_exclusion(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/feature-selection/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["checked"], [self.root.id])
        self.assertEqual(body["excluded"], [self.excluded_child.id])
        self.assertEqual(body["effective"], [self.root.id])

    def test_new_project_inherits_content_override(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/content-overrides/")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["project"], self.new_id)
        self.assertEqual(results[0]["node"], self.root.id)
        self.assertEqual(results[0]["document_type"], "system_test")
        self.assertEqual(results[0]["body"], "覆寫內容：這是專案專屬的測試說明")

    def test_new_project_inherits_custom_section_with_new_id(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/custom-sections/")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "客戶專屬合規聲明")
        self.assertEqual(results[0]["content"], "自訂章節內容")
        self.assertEqual(results[0]["order"], 5)
        # 複製出來的自訂章節必須是全新的 id，不沿用原專案的章節 id。
        self.assertNotEqual(results[0]["id"], self.source_section_id)
        self.new_section_id = results[0]["id"]

    def test_new_project_preview_setting_order_and_exclusion_remapped(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/preview/system_test/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        sections = body["sections"]
        self.assertEqual(len(sections), 2)

        # section_order 順序：自訂章節排在節點章節之前，跟原專案一致
        # （custom: key 已被重寫成新章節 id，仍然排在第一個）。
        self.assertEqual(sections[0]["type"], "custom")
        self.assertEqual(sections[1]["type"], "node")
        self.assertEqual(sections[1]["node"], self.root.id)

        new_section_id = sections[0]["custom_section"]
        self.assertNotEqual(new_section_id, self.source_section_id)

        # excluded_sections 也被重寫成新章節 id 並保留下來：自訂章節仍是
        # included=False，節點章節維持 included=True（未被排除）。
        self.assertFalse(sections[0]["included"])
        self.assertTrue(sections[1]["included"])

        # 內容覆寫也一併生效於新專案的組裝結果中。
        self.assertEqual(sections[1]["body"], "覆寫內容：這是專案專屬的測試說明")

    def test_new_project_preview_does_not_carry_stale_screenshot_selection_or_caption(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/preview/system_test/")
        body = resp.json()
        node_section = next(s for s in body["sections"] if s["type"] == "node")
        self.assertEqual(len(node_section["screenshots"]), 1)
        shot_entry = node_section["screenshots"][0]

        # 新專案沒有任何 ProjectScreenshot，所以沒有圖可套用。
        self.assertFalse(shot_entry["applicable"])
        self.assertIsNone(shot_entry["screenshot"])
        # 圖說覆蓋 (`caption_overrides`) 刻意沒有被複製過來——否則這裡會
        # 誤植出「覆蓋圖說」這個舊專案的圖說文字，但實際上這張截圖需求在
        # 新專案裡根本沒有圖。
        self.assertEqual(shot_entry["caption"], "")

    def test_new_project_screenshot_checklist_entirely_not_uploaded(self):
        resp = self.client.get(f"/api/projects/{self.new_id}/screenshots/")
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        self.assertEqual(len(results), 1)
        items = results[0]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "尚未上傳")
        self.assertIsNone(items[0]["default"])
        self.assertEqual(items[0]["customs"], [])

        # 新專案沒有任何 ProjectScreenshot 列。
        self.assertFalse(
            ProjectScreenshot.objects.filter(project_id=self.new_id).exists()
        )

    def test_source_project_unaffected_after_duplication(self):
        # 功能選擇不變
        selection_resp = self.client.get(
            f"/api/projects/{self.source_id}/feature-selection/"
        )
        self.assertEqual(
            selection_resp.json(),
            {
                "project": self.source_id,
                "checked": [self.root.id],
                "excluded": [self.excluded_child.id],
                "effective": [self.root.id],
            },
        )

        # 內容覆寫不變
        self.assertEqual(
            ProjectFeatureContentOverride.objects.filter(
                project_id=self.source_id
            ).count(),
            1,
        )

        # 自訂章節不變（原 id 仍在）
        self.assertTrue(
            ProjectCustomSection.objects.filter(pk=self.source_section_id).exists()
        )

        # 截圖檢查清單仍是「已完成」，原截圖仍在
        checklist_resp = self.client.get(
            f"/api/projects/{self.source_id}/screenshots/"
        )
        self.assertEqual(
            checklist_resp.json()["results"][0]["items"][0]["status"], "已完成"
        )
        self.assertTrue(
            ProjectScreenshot.objects.filter(pk=self.source_shot_id).exists()
        )

        # 原專案變數完全不變
        source_detail = self.client.get(f"/api/projects/{self.source_id}/").json()
        for field, value in SOURCE_PROJECT_PAYLOAD.items():
            self.assertEqual(source_detail[field], value)

        # 原專案的預覽設定（含 screenshot_selections/caption_overrides）不變
        preview_resp = self.client.get(
            f"/api/projects/{self.source_id}/preview/system_test/"
        )
        node_section = next(
            s for s in preview_resp.json()["sections"] if s["type"] == "node"
        )
        shot_entry = node_section["screenshots"][0]
        self.assertTrue(shot_entry["applicable"])
        self.assertEqual(shot_entry["caption"], "覆蓋圖說")


class ProjectDuplicateValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pm2", password="pw12345")
        self.client.login(username="pm2", password="pw12345")
        create_resp = self.client.post(
            "/api/projects/",
            data=json.dumps(SOURCE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        self.source_id = create_resp.json()["id"]

    def test_duplicate_requires_customer_name_and_project_name(self):
        resp = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        # 沒有多出一筆新專案。
        self.assertEqual(Project.objects.count(), 1)

    def test_duplicate_missing_project_name_only_rejected(self):
        resp = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Project.objects.count(), 1)

    def test_duplicate_nonexistent_project_returns_404(self):
        resp = self.client.post(
            "/api/projects/999999/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_duplicate_soft_deleted_project_returns_404(self):
        self.client.delete(f"/api/projects/{self.source_id}/")
        resp = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_duplicate_with_no_extra_data_still_succeeds(self):
        """一個沒有任何功能勾選/覆寫/章節/預覽設定/截圖的「空」專案，複製
        起來應該一樣成功（只是新專案的關聯資料都是空清單）。"""
        resp = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        new_id = resp.json()["id"]

        selection_resp = self.client.get(
            f"/api/projects/{new_id}/feature-selection/"
        )
        self.assertEqual(selection_resp.json()["checked"], [])
        self.assertEqual(selection_resp.json()["excluded"], [])

        sections_resp = self.client.get(f"/api/projects/{new_id}/custom-sections/")
        self.assertEqual(sections_resp.json()["results"], [])


class ProjectDuplicateAuthGateTests(TestCase):
    def test_duplicate_requires_auth(self):
        owner = User.objects.create_user(username="owner3", password="pw12345")
        project = Project.objects.create(
            owner=owner, customer_name="X", project_name="Y"
        )
        response = self.client.post(
            f"/api/projects/{project.id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class ProjectDuplicateCsrfProtectionTests(TestCase):
    """同 `test_project_api.py::ProjectCsrfProtectionTests` 的既有慣例：
    複製是狀態改變的動作，必須受 CSRF 保護、不可 `@csrf_exempt`。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm3", password="pw12345")
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm3", password="pw12345")

        loose_client = Client()
        loose_client.login(username="pm3", password="pw12345")
        create_resp = loose_client.post(
            "/api/projects/",
            data=json.dumps(SOURCE_PROJECT_PAYLOAD),
            content_type="application/json",
        )
        self.source_id = create_resp.json()["id"]

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_duplicate_without_csrf_token_is_rejected(self):
        response = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Project.objects.count(), 1)

    def test_duplicate_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()
        response = self.client.post(
            f"/api/projects/{self.source_id}/duplicate/",
            data=json.dumps({"customer_name": "新客戶", "project_name": "新專案"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Project.objects.count(), 2)
