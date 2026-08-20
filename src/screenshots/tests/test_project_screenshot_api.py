"""HTTP-layer tests for T5's project-side screenshot checklist and upload
management: checklist derivation from the project's effective feature
selection (reusing `selections.compute_effective_selection`), upload /
replace / delete of the default version, the default + custom-scoped
version coexistence rule, and upload failure handling.

Per the MVP spec's Testing Decisions, the HTTP API layer is the only test
seam. Uploads are simulated with `SimpleUploadedFile` over the Django test
client (per the ticket's file-upload testing notes).

`MEDIA_ROOT` is overridden to a throwaway temp directory for the whole
module so uploaded test fixtures never land under the real `src/media/`
working directory.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from features.models import FeatureNode
from projects.models import Project
from selections.models import ProjectFeatureSelection

from ..models import ProjectScreenshot, ScreenshotRequirement

_TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="gen_doc_screenshots_test_")

# 最小合法 PNG 檔頭（89 50 4E 47 0D 0A 1A 0A）+ 一些填充 bytes——足以通過
# view 層的 magic-bytes 簽章驗證，不需要真的是可被 Pillow 解碼的完整圖檔
# （view 層本來就不用 Pillow，見 models.py 的判斷）。
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _valid_png(name="shot.png"):
    return SimpleUploadedFile(
        name, _PNG_HEADER + b"\x00" * 64, content_type="image/png"
    )


def _text_file_disguised_as_png(name="not-really-a-png.png"):
    """副檔名是 .png，但內容其實是純文字——必須被拒絕（Issue #6
    acceptance criterion 5）。"""
    return SimpleUploadedFile(
        name, b"this is just plain text, not an image", content_type="image/png"
    )


def _txt_file(name="notes.txt"):
    return SimpleUploadedFile(name, b"hello", content_type="text/plain")


def tearDownModule():
    shutil.rmtree(_TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ScreenshotChecklistDerivationTests(TestCase):
    """Issue #6 acceptance criterion 1：專案勾選了「帳號管理」後，截圖管
    理頁自動列出該節點定義的所有截圖需求項目，並標示已完成/尚未上傳狀
    態——直接重用 `selections.compute_effective_selection`，不重新實作
    選擇邏輯。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm1", password="pw12345")
        self.client.login(username="pm1", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )

        self.account_mgmt = FeatureNode.objects.create(name="帳號管理")
        self.reqs = [
            ScreenshotRequirement.objects.create(
                node=self.account_mgmt, name=name, order=i
            )
            for i, name in enumerate(
                ["帳號列表", "新增帳號", "編輯帳號", "刪除帳號確認"]
            )
        ]

        # 不相關節點的截圖需求，未勾選時不該出現在清單中。
        self.unrelated_node = FeatureNode.objects.create(name="報表")
        ScreenshotRequirement.objects.create(node=self.unrelated_node, name="報表畫面")

    def _checklist(self):
        return self.client.get(f"/api/projects/{self.project.id}/screenshots/")

    def test_unchecked_project_has_empty_checklist(self):
        response = self._checklist()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_checking_node_lists_all_its_requirements_as_not_uploaded(self):
        ProjectFeatureSelection.objects.create(
            project=self.project, node=self.account_mgmt
        )

        response = self._checklist()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(len(body["results"]), 1)
        node_entry = body["results"][0]
        self.assertEqual(node_entry["node"]["id"], self.account_mgmt.id)

        item_names = [item["requirement"]["name"] for item in node_entry["items"]]
        self.assertEqual(
            item_names, ["帳號列表", "新增帳號", "編輯帳號", "刪除帳號確認"]
        )
        self.assertTrue(
            all(item["status"] == "尚未上傳" for item in node_entry["items"])
        )

        # 不相關節點的截圖需求不會出現。
        node_ids_in_results = [entry["node"]["id"] for entry in body["results"]]
        self.assertNotIn(self.unrelated_node.id, node_ids_in_results)

    def test_disabled_requirement_is_excluded_from_checklist(self):
        ProjectFeatureSelection.objects.create(
            project=self.project, node=self.account_mgmt
        )
        disabled_req = self.reqs[-1]
        disabled_req.is_enabled = False
        disabled_req.save(update_fields=["is_enabled"])

        response = self._checklist()
        item_names = [
            item["requirement"]["name"]
            for item in response.json()["results"][0]["items"]
        ]
        self.assertNotIn(disabled_req.name, item_names)

    def test_checklist_requires_auth(self):
        self.client.logout()
        response = self._checklist()
        self.assertEqual(response.status_code, 401)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ScreenshotUploadReplaceDeleteTests(TestCase):
    """Issue #6 acceptance criterion 2：使用者可對每個截圖需求上傳、預
    覽、更換、刪除圖片。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm2", password="pw12345")
        self.client.login(username="pm2", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表"
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def _default_url(self):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/default/"
        )

    def test_upload_flips_checklist_status_to_completed_and_is_previewable(self):
        response = self.client.post(self._default_url(), {"image": _valid_png()})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIsNotNone(body["image_url"])
        self.assertFalse(body["is_custom"])

        checklist = self.client.get(
            f"/api/projects/{self.project.id}/screenshots/"
        ).json()
        item = checklist["results"][0]["items"][0]
        self.assertEqual(item["status"], "已完成")
        self.assertIsNotNone(item["default"])
        self.assertIsNotNone(item["default"]["image_url"])

        # 「預覽」：可透過 detail 端點直接取回目前的圖片資訊。
        preview = self.client.get(self._default_url())
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["id"], body["id"])

    def test_upload_defaults_to_all_document_types_when_unspecified(self):
        response = self.client.post(self._default_url(), {"image": _valid_png()})
        body = response.json()
        self.assertEqual(
            sorted(body["document_types"]),
            sorted(
                ["system_design", "system_test", "system_install", "training_deck"]
            ),
        )

    def test_replace_overwrites_existing_default_not_creates_second_row(self):
        first = self.client.post(
            self._default_url(), {"image": _valid_png("first.png")}
        ).json()

        second = self.client.post(
            self._default_url(), {"image": _valid_png("second.png")}
        )
        self.assertEqual(second.status_code, 200)  # 更換用 200，不是 201
        second_body = second.json()

        self.assertEqual(first["id"], second_body["id"])
        self.assertEqual(
            ProjectScreenshot.objects.filter(
                project=self.project, requirement=self.requirement, is_custom=False
            ).count(),
            1,
        )
        self.assertIn("second.png", second_body["original_filename"])

    def test_delete_removes_default_and_checklist_reverts_to_not_uploaded(self):
        self.client.post(self._default_url(), {"image": _valid_png()})

        response = self.client.delete(self._default_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ProjectScreenshot.objects.filter(
                project=self.project, requirement=self.requirement
            ).exists()
        )

        checklist = self.client.get(
            f"/api/projects/{self.project.id}/screenshots/"
        ).json()
        item = checklist["results"][0]["items"][0]
        self.assertEqual(item["status"], "尚未上傳")

    def test_preview_before_any_upload_404s(self):
        response = self.client.get(self._default_url())
        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ScreenshotCustomVersionCoexistenceTests(TestCase):
    """Issue #6 acceptance criterion 4：為某截圖需求上傳一張圖並套用於
    「測試文件」與「教育訓練簡報」後，再為「教育訓練簡報」額外上傳客製
    版本，之後查詢時「測試文件」仍對應原圖、「教育訓練簡報」對應客製版
    本——兩者並存，上傳客製版本不影響/刪除預設版本。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm3", password="pw12345")
        self.client.login(username="pm3", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表"
        )
        ProjectFeatureSelection.objects.create(project=self.project, node=self.node)

    def _default_url(self):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/default/"
        )

    def _custom_url(self):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/custom/"
        )

    def _resolve_url(self, document_type):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/resolve/?document_type={document_type}"
        )

    def test_custom_version_for_one_type_does_not_affect_default_for_others(self):
        default_upload = self.client.post(
            self._default_url(),
            {
                "image": _valid_png("original.png"),
                "document_types": ["system_test", "training_deck"],
            },
        )
        self.assertEqual(default_upload.status_code, 201, default_upload.content)
        default_id = default_upload.json()["id"]

        custom_upload = self.client.post(
            self._custom_url(),
            {
                "image": _valid_png("annotated.png"),
                "document_types": ["training_deck"],
            },
        )
        self.assertEqual(custom_upload.status_code, 201, custom_upload.content)
        custom_id = custom_upload.json()["id"]
        self.assertNotEqual(custom_id, default_id)

        # 測試文件仍對應原圖（預設版本）。
        test_doc_resolution = self.client.get(
            self._resolve_url("system_test")
        ).json()
        self.assertTrue(test_doc_resolution["applicable"])
        self.assertEqual(test_doc_resolution["screenshot"]["id"], default_id)
        self.assertFalse(test_doc_resolution["screenshot"]["is_custom"])

        # 教育訓練簡報改用客製版本。
        training_resolution = self.client.get(
            self._resolve_url("training_deck")
        ).json()
        self.assertTrue(training_resolution["applicable"])
        self.assertEqual(training_resolution["screenshot"]["id"], custom_id)
        self.assertTrue(training_resolution["screenshot"]["is_custom"])

        # 預設版本本身完全沒被動到。
        default_row = ProjectScreenshot.objects.get(pk=default_id)
        self.assertEqual(
            sorted(default_row.document_types), ["system_test", "training_deck"]
        )
        self.assertIn("original.png", default_row.original_filename)

        # 沒被任何一方涵蓋的文件類型查無圖片。
        install_resolution = self.client.get(
            self._resolve_url("system_install")
        ).json()
        self.assertFalse(install_resolution["applicable"])
        self.assertIsNone(install_resolution["screenshot"])

    def test_custom_upload_requires_at_least_one_document_type(self):
        response = self.client.post(
            self._custom_url(), {"image": _valid_png()}
        )
        self.assertEqual(response.status_code, 400)

    def test_overlapping_custom_upload_for_same_type_is_rejected(self):
        self.client.post(
            self._custom_url(),
            {"image": _valid_png("v1.png"), "document_types": ["training_deck"]},
        )
        response = self.client.post(
            self._custom_url(),
            {"image": _valid_png("v2.png"), "document_types": ["training_deck"]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            ProjectScreenshot.objects.filter(
                project=self.project, requirement=self.requirement, is_custom=True
            ).count(),
            1,
        )

    def test_deleting_custom_version_leaves_default_untouched(self):
        default = self.client.post(
            self._default_url(), {"image": _valid_png("default.png")}
        ).json()
        custom = self.client.post(
            self._custom_url(),
            {"image": _valid_png("custom.png"), "document_types": ["training_deck"]},
        ).json()

        delete_response = self.client.delete(
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/custom/{custom['id']}/"
        )
        self.assertEqual(delete_response.status_code, 200)

        self.assertTrue(
            ProjectScreenshot.objects.filter(
                project=self.project, requirement=self.requirement, is_custom=False
            ).exists()
        )
        # 客製版本被刪除後，training_deck 退回使用預設版本（預設版本套用
        # 全部四種文件類型，因為這裡上傳時沒有縮小 document_types 範圍）
        # ——預設版本本身完全沒被客製版本的上傳/刪除影響過。
        resolution = self.client.get(self._resolve_url("training_deck")).json()
        self.assertTrue(resolution["applicable"])
        self.assertEqual(resolution["screenshot"]["id"], default["id"])
        self.assertFalse(resolution["screenshot"]["is_custom"])


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ScreenshotUploadFailureTests(TestCase):
    """Issue #6 acceptance criterion 5：上傳失敗時顯示明確錯誤訊息（不
    需自動重試——這裡只驗證失敗會回傳清楚的錯誤訊息，不驗證任何重試行
    為，因為 MVP 明確不做）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm4", password="pw12345")
        self.client.login(username="pm4", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表"
        )

    def _default_url(self):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/default/"
        )

    def test_wrong_extension_is_rejected_with_clear_message(self):
        response = self.client.post(self._default_url(), {"image": _txt_file()})
        self.assertEqual(response.status_code, 400)
        self.assertIn("不支援的檔案格式", response.json()["detail"])
        self.assertFalse(ProjectScreenshot.objects.exists())

    def test_text_content_disguised_with_image_extension_is_rejected(self):
        response = self.client.post(
            self._default_url(), {"image": _text_file_disguised_as_png()}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不是有效的圖片格式", response.json()["detail"])
        self.assertFalse(ProjectScreenshot.objects.exists())

    def test_missing_file_is_rejected_with_clear_message(self):
        response = self.client.post(self._default_url(), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.json()["detail"])


@override_settings(MEDIA_ROOT=_TEST_MEDIA_ROOT)
class ScreenshotCsrfProtectionTests(TestCase):
    """寫入端點必須受 CSRF 保護（同專案既有慣例）。"""

    def setUp(self):
        self.user = User.objects.create_user(username="pm5", password="pw12345")
        self.project = Project.objects.create(
            owner=self.user, customer_name="客戶", project_name="專案"
        )
        self.node = FeatureNode.objects.create(name="帳號管理")
        self.requirement = ScreenshotRequirement.objects.create(
            node=self.node, name="帳號列表"
        )
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="pm5", password="pw12345")

    def _default_url(self):
        return (
            f"/api/projects/{self.project.id}/screenshots/"
            f"{self.requirement.id}/default/"
        )

    def _csrf_token(self):
        return self.client.get("/api/auth/csrf/").json()["csrfToken"]

    def test_upload_without_csrf_token_is_rejected(self):
        response = self.client.post(self._default_url(), {"image": _valid_png()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectScreenshot.objects.exists())

    def test_upload_with_primed_csrf_token_succeeds(self):
        token = self._csrf_token()
        response = self.client.post(
            self._default_url(),
            {"image": _valid_png()},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.content)
