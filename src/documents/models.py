import uuid

from django.conf import settings
from django.db import models

from features.models import DOCUMENT_TYPE_CHOICES, PROSE_DOCUMENT_TYPES
from projects.models import Project

# T8 scope (Issue #9): DOCX 文件產生與歷史紀錄。
#
# 只有 DOCX 家族三種文件類型（系統設計／系統測試／系統安裝）在本票支援
# 「產生」——教育訓練簡報（PPTX）是 T9 的範圍。這裡沿用
# `features.PROSE_DOCUMENT_TYPES`（跟 T7 判斷「這個文件類型用散文欄位」
# 是同一組常數）當作「本票支援產生的文件類型」，避免另開一份重複清單、
# 兩份定義漂移不同步。
DOCX_GENERATABLE_DOCUMENT_TYPES = PROSE_DOCUMENT_TYPES

OUTPUT_FORMAT_DOCX = "docx"
OUTPUT_FORMAT_PPTX = "pptx"
OUTPUT_FORMAT_CHOICES = [
    (OUTPUT_FORMAT_DOCX, "DOCX"),
    (OUTPUT_FORMAT_PPTX, "PPTX"),
]


def _generated_document_upload_path(instance: "GeneratedDocument", filename: str) -> str:
    # 用 uuid4 當檔名（而非沿用原始 filename）：同一個 (project,
    # document_type) 會反覆重新產生很多次（每次都是新的一筆 append-only
    # 歷史紀錄），用可預期的檔名容易撞名或被覆蓋；uuid4 保證每次產生都是
    # 獨一無二的實體檔案，也讓「舊紀錄的檔案不受後續重新產生影響」這件事
    # 在檔案系統層級也成立，不只是資料庫層級。
    return (
        f"generated_documents/project_{instance.project_id}/"
        f"{instance.document_type}/{uuid.uuid4().hex}.{instance.output_format}"
    )


class GeneratedDocument(models.Model):
    """一次「產生文件」動作的歷史紀錄（append-only，永不覆蓋——見 Issue #1
    Version Strategy／Issue #9 acceptance criteria）。

    設計判斷（T9 重用性，按 Issue #9 要求誠實標註）：這個 model 刻意做成
    「文件類型無關」的通用歷史紀錄容器，而不是只為 DOCX 三類設計：
    - `document_type` 欄位直接沿用 `features.DOCUMENT_TYPE_CHOICES`
      （四個選項都合法），不是只給 DOCX 三類的子集合。
    - `output_format` 欄位讓同一張表未來也能承載 T9 的 PPTX 產出，不需要
      重新設計或搬遷既有歷史資料。
    - `content_snapshot` 存的是 `assembly.assemble_document()` 的完整回傳
      形狀（dict），這個形狀本身對 DOCX／PPTX 都通用（`body` 給散文、
      `bullets` 給條列，見 T7 的既有設計），T9 不需要為了存快照另開欄位。
    - 唯一「T8 專屬」的地方是 view 層：`documents/views.py` 的產生端點目前
      只接受 `DOCX_GENERATABLE_DOCUMENT_TYPES`（三種 DOCX 類型），
      `training_deck` 會被明確擋下並回傳「尚未支援」錯誤——但擋的是「能不
      能呼叫產生端點」，不是這個 model 本身的限制。T9 開發時，如果沿用同
      一個 model，只需要在 view 層放行 `training_deck` 並實作 PPTX
      render 函式，不需要動這裡的 schema。

    `content_snapshot` 在產生當下寫入，之後即使共用內容或專案覆寫被修改，
    這筆已寫入的紀錄不受影響（因為它是資料庫裡的靜態 JSON 值，不是查詢當
    下才運算的東西）——這是「快照」語意成立的關鍵，也是 Issue #9
    acceptance criterion 2（修改客戶名稱重新產生會新增一筆新紀錄、先前紀
    錄內容不變）能夠通過的根本原因。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="generated_documents"
    )
    document_type = models.CharField(
        "文件類型", max_length=30, choices=DOCUMENT_TYPE_CHOICES
    )
    output_format = models.CharField(
        "輸出格式", max_length=10, choices=OUTPUT_FORMAT_CHOICES, default=OUTPUT_FORMAT_DOCX
    )

    # 產生當下的完整組裝內容快照（`assemble_document()` 的回傳值），
    # JSON 儲存——見 Issue #9 What to build 對快照格式的建議。
    content_snapshot = models.JSONField("內容快照")

    file = models.FileField("產生的檔案", upload_to=_generated_document_upload_path)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    generated_at = models.DateTimeField("產生時間", auto_now_add=True)

    class Meta:
        ordering = ["-generated_at", "-id"]

    def __str__(self) -> str:
        return f"{self.project_id} / {self.document_type} @ {self.generated_at}"
