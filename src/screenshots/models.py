from django.db import models

from features.models import FeatureNode
from projects.models import Project


class ScreenshotRequirement(models.Model):
    """掛在某個 FeatureNode 上的截圖需求定義（例如「帳號管理」節點需要
    「帳號列表／新增帳號／編輯帳號／刪除帳號確認」）。

    刻意**不綁文件類型**（見 Issue #1 Screenshot Rules）——一張截圖預設可
    被多種文件類型共用，「套用在哪些文件類型」是 `ProjectScreenshot` 上傳
    當下才決定的事，不是這張需求定義本身的屬性。

    `is_enabled` 是軟停用旗標，比照 `FeatureNode`／`ProjectFeatureSelection`
    的既有慣例：停用後不再出現在「本節點需要哪些截圖」的可選/可勾清單中，
    但既有的 `ProjectScreenshot` 關聯與已產生文件的快照不受影響。這是本
    票的設計判斷（issue 本身沒有明講要不要支援刪除需求項目），選擇軟刪除
    是為了避免刪掉需求定義時，連帶讓專案既有已上傳的截圖失去歸屬。
    """

    node = models.ForeignKey(
        FeatureNode, on_delete=models.CASCADE, related_name="screenshot_requirements"
    )
    name = models.CharField("截圖項目名稱", max_length=200)
    order = models.PositiveIntegerField("排序", default=0)
    is_enabled = models.BooleanField("是否啟用", default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["node_id", "order", "id"]
        verbose_name = "截圖需求"
        verbose_name_plural = "截圖需求"

    def __str__(self) -> str:
        return f"{self.node.name} / {self.name}"


def _screenshot_upload_path(instance: "ProjectScreenshot", filename: str) -> str:
    kind = "custom" if instance.is_custom else "default"
    return (
        f"screenshots/project_{instance.project_id}/"
        f"requirement_{instance.requirement_id}/{kind}/{filename}"
    )


class ProjectScreenshot(models.Model):
    """專案針對某 `ScreenshotRequirement` 上傳的實際圖片版本。

    每個 (project, requirement) 最多有一筆 `is_custom=False` 的「預設版
    本」——上傳時可勾選要套用在哪些文件類型（`document_types`），預設全選
    （見 Issue #6 acceptance criteria）。同一個 (project, requirement) 之
    下還可以有任意數量 `is_custom=True` 的「客製版本」，各自套用在自己指
    定的文件類型子集合，與預設版本並存、互不影響——上傳客製版本不會刪除
    或修改預設版本（見 Issue #6 acceptance criterion 4：「教育訓練簡報」
    另外上傳客製版本後，「測試文件」仍對應原圖）。

    查詢某個 (project, requirement, document_type) 實際套用哪一張圖片時，
    客製版本優先於預設版本——見 `resolve_applicable_screenshot()`。
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="screenshots"
    )
    requirement = models.ForeignKey(
        ScreenshotRequirement, on_delete=models.CASCADE, related_name="+"
    )

    # 刻意用 FileField 而非 ImageField：ImageField 需要 Pillow 才能通過
    # Django 的系統檢查（見 requirements.txt，目前未安裝 Pillow），而檔案
    # 類型驗證本來就要在 view 層手動做「明確錯誤訊息」（見 Issue #6
    # acceptance criterion 5），沒有理由為了 ImageField 額外引入新依賴。
    image = models.FileField("圖片", upload_to=_screenshot_upload_path)
    original_filename = models.CharField(max_length=255, blank=True, default="")

    # 這張圖套用在哪些文件類型，儲存 document_type code 的陣列（見
    # features.models.DOCUMENT_TYPE_CODES）。預設版本預設全選四種；客製
    # 版本必須至少指定一種，通常只指定一種（見 view 層驗證）。
    document_types = models.JSONField("套用文件類型", default=list)

    is_custom = models.BooleanField("是否為客製版本", default=False)

    # 圖說：對應 Issue #1 Core Domain Model 的「圖片說明（持久化、可調
    # 整）」。T5 acceptance criteria 未針對圖說本身訂驗收情境（那是 T7
    # 文件預覽頁「圖片與圖說調整」的範圍），這裡先把欄位留好、可選填。
    caption = models.CharField("圖說", max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "requirement"],
                condition=models.Q(is_custom=False),
                name="unique_default_screenshot_per_requirement",
            )
        ]
        ordering = ["requirement_id", "is_custom", "id"]
        verbose_name = "專案截圖"
        verbose_name_plural = "專案截圖"

    def __str__(self) -> str:
        kind = "客製" if self.is_custom else "預設"
        return f"{self.project_id} / {self.requirement_id} / {kind}"


def resolve_applicable_screenshot(
    project: Project, requirement: ScreenshotRequirement, document_type: str
) -> "ProjectScreenshot | None":
    """回傳 (project, requirement) 在指定 `document_type` 下實際套用的
    `ProjectScreenshot`，找不到任何一張圖套用則回傳 None。

    客製版本優先於預設版本：先找涵蓋這個 document_type 的客製版本，找到
    就直接回傳；否則退回預設版本（若預設版本本身有涵蓋這個 document_type）
    （見 Issue #6 acceptance criterion 4 的並存規則）。

    這張表的資料量（每個 project+requirement 最多幾筆）很小，直接在
    Python 端做過濾，不依賴各資料庫後端對 JSONField 的 containment 查詢
    語法差異（同 `selections.models.compute_effective_selection` 的既有
    設計判斷：小資料量換取查詢邏輯的可攜性與可讀性）。
    """
    for shot in ProjectScreenshot.objects.filter(
        project=project, requirement=requirement, is_custom=True
    ):
        if document_type in shot.document_types:
            return shot

    default = ProjectScreenshot.objects.filter(
        project=project, requirement=requirement, is_custom=False
    ).first()
    if default is not None and document_type in default.document_types:
        return default

    return None
