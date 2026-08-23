import json

from django.contrib import admin, messages
from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import path, reverse

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """在專案的後台編輯頁加上四個「產生文件」按鈕，讓使用者不需要打 API
    就能觸發文件產生——後台原本的 CRUD 表單涵蓋了專案/功能/截圖等資料
    管理，但「產生文件」是一個動作而不是一筆資料，Django admin 的通用
    CRUD 介面本來就沒有對應的按鈕，這裡用自訂 admin view 補上。

    刻意直接呼叫 `documents.views.generate_document`（既有的 API view
    function）而不是重新實作一份產生邏輯：驗證（`validate_generation_
    readiness`）、渲染、快照寫入都跟正式 API 走同一條路徑，保證後台按鈕
    產生的結果跟直接呼叫 API 完全一致，不會有兩套邏輯漂移的風險。
    """

    change_form_template = "admin/projects/project/change_form.html"

    # 專案刪除為真實刪除（無 soft-delete）：後台的「刪除」按鈕沿用 Django
    # admin 預設行為（呼叫 model 的 .delete()），跟 API 的 DELETE /
    # projects/<id>/ 語意一致——請注意這是不可復原的操作，且
    # `GeneratedDocument.project` 為 on_delete=CASCADE，刪除專案會一併永久
    # 刪除該專案的所有文件產生歷史。

    def change_view(self, request, object_id, form_url="", extra_context=None):
        from features.models import DOCUMENT_TYPE_CHOICES

        extra_context = extra_context or {}
        extra_context["generate_document_types"] = DOCUMENT_TYPE_CHOICES
        return super().change_view(request, object_id, form_url, extra_context)

    def get_urls(self):
        custom = [
            path(
                "<int:object_id>/generate/<str:document_type>/",
                self.admin_site.admin_view(self.generate_document_view),
                name="projects_project_generate_document",
            ),
        ]
        return custom + super().get_urls()

    def generate_document_view(self, request: HttpRequest, object_id, document_type):
        from documents.views import generate_document

        change_url = reverse("admin:projects_project_change", args=[object_id])

        if request.method != "POST":
            return redirect(change_url)

        response = generate_document(request, object_id, document_type)

        if response.status_code != 200:
            try:
                detail = json.loads(response.content)
            except (ValueError, TypeError):
                detail = {"detail": "產生文件失敗"}

            message = detail.get("detail", "產生文件失敗")
            missing_screenshots = detail.get("missing_screenshots")
            missing_variables = detail.get("missing_variables")
            if missing_screenshots:
                names = "、".join(m["requirement_name"] for m in missing_screenshots)
                message += f"（缺少截圖：{names}）"
            if missing_variables:
                message += f"（缺少變數：{'、'.join(missing_variables)}）"

            messages.error(request, message)
            return redirect(change_url)

        return response
